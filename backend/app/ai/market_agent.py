"""Market (大盘) analysis agent (BP-V2-010).

每日大盘研判：指数趋势、市场情绪、成交量、赚钱效应、仓位建议。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Iterator

from sqlalchemy.orm import Session

from app.ai import llm_client
from app.models.sentiment import SaMarketSentiment
from app.services import market_service
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

logger = logging.getLogger(__name__)


def gather_context(db: Session, target: str | None) -> dict:
    indices = market_service.get_indices(db)
    summary = market_service.get_market_summary(db)
    sentiment = db.execute(
        select(SaMarketSentiment).order_by(SaMarketSentiment.trade_date.desc()).limit(1)
    ).scalar_one_or_none()
    return {
        "indices": [{"name": i["name"], "close": i["close"], "pct": i["pct_change"]} for i in indices],
        "breadth": summary,
        "sentiment": {
            "limit_up": sentiment.limit_up_count if sentiment else None,
            "seal_rate": float(sentiment.seal_rate) if sentiment and sentiment.seal_rate else None,
            "max_streak": sentiment.max_streak if sentiment else None,
        } if sentiment else None,
    }


def build_prompt(ctx: dict) -> str:
    return (
        "你是 A 股大盘分析专家。根据今日市场数据，研判大盘走势并给出仓位建议。用 markdown 输出。\n\n"
        f"指数: {ctx.get('indices')}\n"
        f"涨跌家数/成交额: {ctx.get('breadth')}\n\n"
        "输出:\n## 指数趋势\n## 市场情绪\n## 成交量\n## 赚钱效应\n## 仓位建议\n\n"
        "末尾: ⚠ 以上为 AI 生成的参考信号，不构成投资建议"
    )


def run(db: Session, target: str | None) -> dict:
    ctx = gather_context(db, target)
    text = llm_client.chat([SystemMessage(content="你是专业 A 股投顾"),
                            HumanMessage(content=build_prompt(ctx))])
    return {
        "trade_date": date.today(),
        "title": "每日大盘研判",
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
