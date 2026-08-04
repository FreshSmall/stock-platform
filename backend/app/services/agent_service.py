"""V2 multi-agent report service (BP-V2-009~012).

Four agents share a uniform orchestration:
    gather_context(db, target) -> prompt -> LLM -> parse -> persist sa_agent_report

- sector  : 板块分析 Agent (BP-V2-009)
- market  : 大盘分析 Agent (BP-V2-010)
- review  : 每日复盘 Agent (BP-V2-011)
- recommend: 股票推荐 Agent (BP-V2-012)

Each agent is a thin module exposing ``gather_context`` + ``build_prompt`` +
``run(db, target)``; this service dispatches and persists.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Iterator

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai import llm_client
from app.models.agent import SaAgentReport

logger = logging.getLogger(__name__)

AGENTS = ("sector", "market", "review", "recommend")


def list_reports(
    db: Session,
    agent: str | None = None,
    trade_date: date | None = None,
    limit: int = 20,
) -> list[dict]:
    """List reports, optionally filtered by agent/date."""
    stmt = select(SaAgentReport)
    if agent:
        stmt = stmt.where(SaAgentReport.agent == agent)
    if trade_date:
        stmt = stmt.where(SaAgentReport.trade_date == trade_date)
    stmt = stmt.order_by(desc(SaAgentReport.created_at)).limit(limit)
    rows = db.execute(stmt).scalars().all()
    return [_report_to_dict(r) for r in rows]


def get_report(db: Session, report_id: int) -> dict | None:
    r = db.get(SaAgentReport, report_id)
    return _report_to_dict(r) if r else None


def generate(db: Session, agent: str, target: str | None = None) -> dict:
    """Run an agent synchronously, persist the report, return it.

    LLM errors are caught and a minimal error report is persisted so the caller
    still gets a row (the report ``summary`` carries the error).
    """
    if agent not in AGENTS:
        raise ValueError(f"unknown agent: {agent}")

    # Lazy import to keep this module light and avoid circulars.
    from app.ai import market_agent, recommend_agent, review_agent, sector_agent

    modules = {
        "sector": sector_agent,
        "market": market_agent,
        "review": review_agent,
        "recommend": recommend_agent,
    }
    mod = modules[agent]
    today = date.today()
    try:
        result = mod.run(db, target)
        report = SaAgentReport(
            agent=agent,
            trade_date=result.get("trade_date", today),
            title=result.get("title"),
            target=target,
            summary=result.get("summary"),
            content=result.get("content"),
            scores=result.get("scores"),
        )
    except Exception as e:  # noqa: BLE001 - record failure as a report
        logger.exception("agent %s failed", agent)
        report = SaAgentReport(
            agent=agent,
            trade_date=today,
            title=f"{agent} agent 执行失败",
            target=target,
            summary=f"生成失败: {e}",
            content=None,
            scores=None,
        )
    db.add(report)
    db.commit()
    db.refresh(report)
    return _report_to_dict(report)


def stream_report(
    db: Session, agent: str, target: str | None = None
) -> Iterator[tuple[str, str]]:
    """Stream an agent's markdown via SSE-style (event, chunk) tuples.

    Each agent module exposes ``stream(db, target)`` yielding markdown chunks;
    we wrap with start/done events. (Not persisted — use ``generate`` for that.)
    """
    if agent not in AGENTS:
        raise ValueError(f"unknown agent: {agent}")
    from app.ai import market_agent, recommend_agent, review_agent, sector_agent

    modules = {
        "sector": sector_agent,
        "market": market_agent,
        "review": review_agent,
        "recommend": recommend_agent,
    }
    yield ("start", f"{agent} agent 开始分析")
    try:
        for chunk in modules[agent].stream(db, target):
            yield ("chunk", chunk)
    except Exception as e:  # noqa: BLE001
        yield ("error", str(e))
    yield ("done", "⚠ 仅供参考，不构成投资建议")


def _report_to_dict(r: SaAgentReport) -> dict:
    return {
        "id": r.id,
        "agent": r.agent,
        "trade_date": r.trade_date.isoformat() if r.trade_date else None,
        "title": r.title,
        "target": r.target,
        "summary": r.summary,
        "content": r.content,
        "scores": r.scores,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
