"""Daily review agent (BP-V2-011).

每日复盘：今日热点、龙头股、炸板原因、资金流向、明日预判。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Iterator

from sqlalchemy.orm import Session

from app.ai import llm_client
from app.services import market_service, sentiment_service
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


def gather_context(db: Session, target: str | None) -> dict:
    summary = market_service.get_market_summary(db)
    hot = market_service.get_hot_stocks(db, sort="amount", limit=10)
    gainers = market_service.get_hot_stocks(db, sort="pct_change", limit=10)
    return {
        "breadth": summary,
        "hot_by_amount": [{"code": s["stock_code"], "name": s.get("stock_name"), "amount": s.get("amount")} for s in hot],
        "top_gainers": [{"code": s["stock_code"], "name": s.get("stock_name"), "pct": s.get("pct_change")} for s in gainers],
    }


def build_prompt(ctx: dict) -> str:
    return (
        "你是 A 股复盘专家。根据今日市场数据，生成每日复盘报告。用 markdown 输出。\n\n"
        f"涨跌家数/成交额: {ctx.get('breadth')}\n"
        f"成交额Top: {ctx.get('hot_by_amount')}\n"
        f"涨幅Top: {ctx.get('top_gainers')}\n\n"
        "输出:\n## 今日热点\n## 龙头股\n## 资金流向\n## 明日预判\n\n"
        "末尾: ⚠ 以上为 AI 生成的参考信号，不构成投资建议"
    )


def run(db: Session, target: str | None) -> dict:
    ctx = gather_context(db, target)
    text = llm_client.chat([SystemMessage(content="你是专业 A 股投顾"),
                            HumanMessage(content=build_prompt(ctx))])
    return {
        "trade_date": date.today(),
        "title": "每日复盘",
        "summary": text[:200] if text else None,
        "content": text,
    }


def stream(db: Session, target: str | None) -> Iterator[str]:
    ctx = gather_context(db, target)
    for chunk in llm_client.stream_chat(
        [SystemMessage(content="你是专业 A 股投顾"),
         HumanMessage(content=build_prompt(ctx))]
    ):
        yield chunk
