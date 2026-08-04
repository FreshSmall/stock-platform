"""Stock recommendation agent (BP-V2-012).

基于多因子打分给出股票推荐 + 推荐理由 + 风险提示 + 止盈止损/仓位建议。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Iterator

from sqlalchemy.orm import Session

from app.ai import llm_client
from app.services import factor_service
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

# Default multi-factor preset: value (PE lower better) + momentum (RSI reversal).
_DEFAULT_FACTORS = [
    {"code": "pe", "weight": -1.0},
    {"code": "rsi12", "weight": -1.0},
    {"code": "boll_width", "weight": 1.0},
]


def gather_context(db: Session, target: str | None) -> dict:
    """target = optional factor preset name; uses default otherwise."""
    today = date.today()
    ranked = factor_service.multi_factor_score(db, _DEFAULT_FACTORS, today, universe_size=20)
    top_codes = [r["stock_code"] for r in ranked[:5]]
    # attach factor values for the top picks
    picks = []
    for r in ranked[:5]:
        code = r["stock_code"]
        pe = factor_service.compute_series(db, "pe", code, today, today)
        picks.append({"code": code, "score": r["score"]})
    return {"top_picks": picks, "universe": len(ranked)}


def build_prompt(ctx: dict) -> str:
    return (
        "你是 A 股投资顾问。根据多因子打分排名，对前 5 只股票给出推荐分析。用 markdown 输出。\n\n"
        f"多因子打分Top5: {ctx.get('top_picks')}\n\n"
        "对每只股票输出:\n- 推荐理由\n- 风险提示\n- 止盈止损建议\n- 仓位建议\n\n"
        "末尾: ⚠ 以上为 AI 生成的参考信号，不构成投资建议"
    )


def run(db: Session, target: str | None) -> dict:
    ctx = gather_context(db, target)
    text = llm_client.chat([SystemMessage(content="你是专业 A 股投顾"),
                            HumanMessage(content=build_prompt(ctx))])
    picks = ctx.get("top_picks", [])
    return {
        "trade_date": date.today(),
        "title": "多因子股票推荐",
        "target": ",".join(p["code"] for p in picks[:3]) if picks else None,
        "summary": text[:200] if text else None,
        "content": text,
        "scores": {"picks": picks},
    }


def stream(db: Session, target: str | None) -> Iterator[str]:
    ctx = gather_context(db, target)
    for chunk in llm_client.stream_chat(
        [SystemMessage(content="你是专业 A 股投顾"),
         HumanMessage(content=build_prompt(ctx))]
    ):
        yield chunk
