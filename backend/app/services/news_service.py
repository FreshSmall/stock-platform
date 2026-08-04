"""新闻情绪打分 + 列表查询（BP-V2-008）。

* :func:`score_sentiment` —— 调 :mod:`app.ai.llm_client` 对一段资讯文本打
  情绪分（-1~+1）、抽摘要、关联个股/板块。返回结构化 dict，便于
  :mod:`app.data.sync_news` 回填到 ``sa_news_sentiment``。
* :func:`list_news` —— 按个股 / 板块 / 日期查询已入库的资讯列表。

LLM 全部走 :mod:`app.ai.llm_client`，因此测试只要 monkeypatch
``llm_client.chat`` 即可断网运行（参照 :mod:`tests.test_analysis_service`
对 ``stream_chat`` 的 patch 套路）。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import llm_client
from app.models.news import SaNewsSentiment

logger = logging.getLogger(__name__)

# 情绪分允许的取值范围；超界会被夹到 [-1, +1]。
_SENTIMENT_MIN = Decimal("-1")
_SENTIMENT_MAX = Decimal("1")

_SYSTEM_PROMPT = (
    "你是一名 A 股市场新闻情绪分析助手。给定一条财经资讯，请输出严格的 JSON，"
    '字段：{"sentiment": 情绪分(-1~+1, 负=利空/正=利好/0=中性, 保留3位小数), '
    '"summary": 不超过80字的中文摘要, "stock_codes": 关联的6位A股代码数组(无则空数组), '
    '"sector": 关联板块名称(无则空字符串)}。只输出 JSON，不要任何解释或额外文本。'
)


def _clamp_sentiment(v: Any) -> Decimal | None:
    """把 LLM 返回的情绪值规整为 [-1, +1] 内的 Decimal(3 位小数)。"""
    if v is None:
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    if d < _SENTIMENT_MIN:
        d = _SENTIMENT_MIN
    elif d > _SENTIMENT_MAX:
        d = _SENTIMENT_MAX
    return d.quantize(Decimal("0.001"))


def _parse_news_json(text: str) -> dict | None:
    """从 LLM 原始输出里抽出 JSON 对象。

    容忍被 ```json ... ``` 围栏包裹或前后带说明文字（取最大 ``{...}`` 段）。
    """
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def score_sentiment(text: str) -> dict:
    """对一段资讯文本做情绪打分。

    调用 :func:`llm_client.chat`（同步一次性返回），把 LLM 的 JSON 输出解析为
    ``{sentiment, summary, stock_codes, sector}``。任意一步失败都返回空 dict
    （不抛异常）—— 调用方（sync_recent）据此把情绪分留 NULL 继续往下走。

    :param text: 资讯正文（标题 + 内容拼起来的文本）。
    :return: dict，可能含键：
        * ``sentiment``  : Optional[Decimal] —— 夹到 [-1, +1] 的情绪分。
        * ``summary``    : Optional[str]     —— 摘要。
        * ``stock_codes``: list[str]         —— 关联个股代码。
        * ``sector``     : Optional[str]     —— 关联板块。
    """
    if not text or not text.strip():
        return {}

    messages = llm_client.to_messages(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text[:1000]},  # 截断防 token 爆炸
        ]
    )
    try:
        raw = llm_client.chat(messages)
    except Exception as e:
        logger.warning("score_sentiment: LLM 调用失败: %s", e)
        return {}

    parsed = _parse_news_json(raw)
    if parsed is None:
        logger.warning("score_sentiment: 无法解析 LLM 输出: %s", (raw or "")[:200])
        return {}

    codes = parsed.get("stock_codes") or []
    if isinstance(codes, list):
        codes = [str(c).zfill(6) for c in codes if str(c).strip()]
    else:
        codes = []

    sector = parsed.get("sector")
    if isinstance(sector, str):
        sector = sector.strip() or None
    else:
        sector = None

    return {
        "sentiment": _clamp_sentiment(parsed.get("sentiment")),
        "summary": (parsed.get("summary") or "").strip()[:500] or None,
        "stock_codes": codes,
        "sector": sector,
    }


def list_news(
    db: Session,
    stock: str | None = None,
    sector: str | None = None,
    date: date | None = None,
    limit: int = 50,
) -> list[dict]:
    """查询新闻列表，可选按个股 / 板块 / 日期过滤。

    过滤语义：
    * ``stock``  —— 命中 ``stock_codes`` JSON 数组里包含该代码的行（MySQL
      ``JSON_CONTAINS``）；不传则不限。
    * ``sector`` —— ``sector`` 列精确等值。
    * ``date``   —— ``pub_time`` 落在该自然日（00:00:00 ~ 次日 00:00:00）。
    * 结果按 ``pub_time`` 倒序，最多 ``limit`` 条。

    :return: list of dict（含 sentiment / summary 等序列化字段）。
    """
    stmt = select(SaNewsSentiment)
    if stock:
        # JSON_CONTAINS(stock_codes, '"000858"')。占位符无法直接塞进 JSON 字面量，
        # 这里用 func.json_contains + 实参（SQLAlchemy 会按参数绑定）。
        from sqlalchemy import func

        stmt = stmt.where(func.json_contains(SaNewsSentiment.stock_codes, f'"{stock}"'))
    if sector:
        stmt = stmt.where(SaNewsSentiment.sector == sector)
    if date is not None:
        from datetime import datetime, timedelta

        start = datetime(date.year, date.month, date.day)
        end = start + timedelta(days=1)
        stmt = stmt.where(SaNewsSentiment.pub_time >= start).where(
            SaNewsSentiment.pub_time < end
        )
    # MySQL 在 DESC 排序下天然把 NULL 排到最后，与 NULLS LAST 等价；不写
    # ``nullslast()`` 是因为 MySQL 不支持该语法。
    stmt = stmt.order_by(SaNewsSentiment.pub_time.desc()).limit(limit)

    rows = db.execute(stmt).scalars().all()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(r: SaNewsSentiment) -> dict:
    """把 ORM 行序列化为 API 友好的 dict。"""
    return {
        "id": r.id,
        "pub_time": r.pub_time,
        "title": r.title,
        "content": r.content,
        "source": r.source,
        "stock_codes": r.stock_codes or [],
        "sector": r.sector,
        "sentiment": float(r.sentiment) if r.sentiment is not None else None,
        "summary": r.summary,
    }
