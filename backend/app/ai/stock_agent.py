"""Stock analysis agent: data context -> LLM stream -> parse -> result.

Pipeline (all in one generator, :func:`analyze_stream`):

1. Gather structured context for ``code`` (recent K-line bars, MACD, PE/PB,
   money-flow placeholder) via the market/indicator services.
2. Build the LangChain message list (system + user) from the prompts.
3. Stream the LLM, yielding each text chunk as it arrives.
4. Parse the final concatenated text as JSON (tolerant of stray markdown
   fences) and emit a :class:`AnalysisResult`.

All network access is funnelled through :mod:`app.ai.llm_client`, so tests
monkeypatch ``llm_client.stream_chat`` to drive the agent end-to-end without
any real LLM call.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterator

import pandas as pd
from sqlalchemy.orm import Session

from app.ai import llm_client, prompts
from app.services import indicator_service, market_service

logger = logging.getLogger(__name__)


def _gather_context(db: Session, code: str) -> dict:
    """Fetch recent K-line, MACD and PE/PB for ``code``.

    Returns a dict shaped for :func:`prompts.build_analysis_user_prompt`:
    ``{kline_recent, indicators, finance, money_flow}``. ``money_flow`` is
    intentionally empty for now (no money-flow table yet); the placeholder
    keeps the prompt shape stable for downstream wiring.
    """
    end = date.today()
    start = end - timedelta(days=120)
    rows = market_service.get_kline(db, code, start=start, end=end)
    if not rows:
        return {"kline_recent": [], "indicators": {}, "finance": {}, "money_flow": {}}

    kline_recent = [
        {
            "date": r.trade_date.isoformat(),
            "close": float(r.close) if r.close is not None else None,
            "pct_change": float(r.pct_change) if r.pct_change is not None else None,
            "volume": r.volume,
        }
        for r in rows
    ]

    closes = pd.Series([float(r.close) for r in rows if r.close is not None])
    indicators: dict[str, Any] = {}
    if not closes.empty:
        try:
            macd_df = indicator_service.calc_macd(closes)
            last = macd_df.iloc[-1].to_dict()
            indicators["macd"] = {
                k: (None if pd.isna(v) else float(v)) for k, v in last.items()
            }
        except Exception as e:  # MACD is best-effort; never fatal to the agent.
            logger.warning("macd calc failed for %s: %s", code, e)

    info = market_service.get_stock_info(db, code)
    finance: dict[str, float] = {}
    if info is not None:
        for k in ("pe", "pb"):
            v = getattr(info, k, None)
            if v is not None:
                finance[k] = float(v)

    return {
        "kline_recent": kline_recent,
        "indicators": indicators,
        "finance": finance,
        "money_flow": {},
    }


def _parse_analysis_json(text: str) -> dict | None:
    """Extract the analysis JSON object from raw LLM output.

    The LLM is asked to emit JSON only, but defensively tolerate stray
    `````json ... ````` fences or surrounding prose by grabbing the largest
    ``{...}`` span. Returns ``None`` if no valid object is found.
    """
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _to_decimal(value: Any) -> Decimal | None:
    """Coerce ``value`` (int/float/str) to Decimal, returning None on falsy."""
    if value is None:
        return None
    return Decimal(str(value))


def analyze_stream(db: Session, code: str) -> Iterator[tuple[str, Any]]:
    """Generator yielding SSE events for a single-stock analysis.

    Yields ``(event_type, payload)`` tuples where ``event_type`` is one of:

    * ``"context"``  - ``{stock_code, stock_name, bars}`` summarising inputs.
    * ``"chunk"``    - a streamed ``str`` text fragment from the LLM.
    * ``"error"``    - a ``str`` error message (e.g. JSON parse failure).
    * ``"done"``     - the parsed :class:`AnalysisResult`, or ``None`` on
      failure.

    On a successful parse the final event is ``("done", AnalysisResult)``;
    on failure an ``("error", ...)`` precedes ``("done", None)``.
    """
    info = market_service.get_stock_info(db, code)
    stock_name = info.stock_name if info is not None else code
    context = _gather_context(db, code)
    yield (
        "context",
        {
            "stock_code": code,
            "stock_name": stock_name,
            "bars": len(context["kline_recent"]),
        },
    )

    user_prompt = prompts.build_analysis_user_prompt(code, stock_name, context)
    messages = llm_client.to_messages(
        [
            {"role": "system", "content": prompts.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
    )

    full_text_parts: list[str] = []
    for chunk in llm_client.stream_chat(messages):
        full_text_parts.append(chunk)
        yield ("chunk", chunk)

    full_text = "".join(full_text_parts)
    parsed = _parse_analysis_json(full_text)
    if parsed is None:
        yield ("error", "failed to parse LLM JSON output")
        yield ("done", None)
        return

    # Local import to avoid an app.schemas <-> app.ai import cycle at module
    # load time (app.schemas is fine either way, but keeping it lazy matches
    # the pattern used elsewhere in the codebase).
    from app.schemas.ai import AnalysisResult, AnalysisScores

    scores_raw = parsed.get("scores") or {}
    result = AnalysisResult(
        request_id="",  # filled in by the caller (service / API).
        stock_code=code,
        score=_to_decimal(parsed.get("score")),
        scores=AnalysisScores(
            fundamental=_to_decimal(scores_raw.get("fundamental")),
            technical=_to_decimal(scores_raw.get("technical")),
            capital=_to_decimal(scores_raw.get("capital")),
            news=_to_decimal(scores_raw.get("news")),
            risk=_to_decimal(scores_raw.get("risk")),
        ),
        fundamentals=parsed.get("fundamentals"),
        technicals=parsed.get("technicals"),
        capital=parsed.get("capital"),
        news=parsed.get("news"),
        risk=parsed.get("risk"),
    )
    yield ("done", result)
