"""Sector analysis agent (BP-V2-009).

分析一个板块的热度、龙头、补涨股、持续性、趋势、资金流向。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Iterator

from sqlalchemy.orm import Session

from app.ai import llm_client
from app.services import sector_service
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


def gather_context(db: Session, target: str | None) -> dict:
    """Collect sector data for the prompt.

    ``target`` = sector code. Falls back to the top sector if None.
    """
    sectors = sector_service.list_sectors(db, limit=10)
    target_name = target
    detail = None
    if target:
        detail = sector_service.get_sector_detail(db, target)
    elif sectors:
        target_name = sectors[0]["sector_name"]
    stocks = []
    if detail:
        stocks = sector_service.list_sector_stocks(db, target or detail["sector_code"], page=1, size=10)["items"]
    return {
        "target_sector": target_name,
        "top_sectors": [{"name": s["sector_name"], "pct": s.get("pct_change")} for s in sectors[:5]],
        "detail": detail,
        "top_stocks": stocks[:8],
    }


def build_prompt(ctx: dict) -> str:
    return (
        "你是 A 股板块分析专家。根据以下板块数据，分析该板块的热度、龙头股、"
        "补涨机会、持续性、趋势和资金流向。用 markdown 输出。\n\n"
        f"目标板块: {ctx.get('target_sector') or '市场热门板块'}\n"
        f"热门板块: {ctx.get('top_sectors')}\n"
        f"板块详情: {ctx.get('detail')}\n"
        f"成分股(涨幅前): {ctx.get('top_stocks')}\n\n"
        "输出格式:\n## 板块热度\n## 龙头股\n## 补涨机会\n## 持续性研判\n## 资金流向\n\n"
        "末尾固定: ⚠ 以上为 AI 生成的参考信号，不构成投资建议"
    )


def run(db: Session, target: str | None) -> dict:
    """Generate the report (non-streaming)."""
    ctx = gather_context(db, target)
    text = llm_client.chat([SystemMessage(content="你是专业 A 股投顾"),
                            HumanMessage(content=build_prompt(ctx))])
    return {
        "trade_date": date.today(),
        "title": f"{ctx.get('target_sector') or '热门'}板块分析",
        "summary": text[:200] if text else None,
        "content": text,
    }


def stream(db: Session, target: str | None) -> Iterator[str]:
    """Yield markdown chunks."""
    ctx = gather_context(db, target)
    for chunk in llm_client.stream_chat(
        [SystemMessage(content="你是专业 A 股投顾"),
         HumanMessage(content=build_prompt(ctx))]
    ):
        yield chunk
