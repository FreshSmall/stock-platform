"""Admin service (BP-V1.5-010): data-source status, task runs, user management.

Task runs map a logical task name to its sync function (registered below) and
record every execution in ``sa_admin_task_log`` so the admin console can show
history and allow manual re-runs.

V2.1 long tasks (spec-004 §3.4): tasks listed in :data:`_LONG_TASKS` exceed
the 300s synchronous deadline by nature (full-market repair / re-ingest
batches). ``run_task_async`` submits them to a dedicated single-worker
executor and returns the ``sa_admin_task_log`` id immediately; the frontend
polls ``GET /admin/tasks/runs/{run_id}`` for live progress (batches mirror
their progress into the log row).
"""

import json
import logging
from datetime import datetime
from typing import Callable

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.market_data import SaAdminTaskLog
from app.models.user import SaUser

logger = logging.getLogger(__name__)

# Logical task name -> no-arg runner that performs the sync against its own
# session. Runners are registered lazily to avoid importing the (network-bound)
# akshare client at module import time.
_TASK_RUNNERS: dict[str, Callable[[], int]] = {}


def _register_runners() -> None:
    """Populate the task registry on first use (avoids import-time side effects)."""
    if _TASK_RUNNERS:
        return

    def _wrap(name: str, fn):
        def runner() -> int:
            db = SessionLocal()
            try:
                return fn(db)
            finally:
                db.close()

        _TASK_RUNNERS[name] = runner

    from app.data import (
        sync_dragon_tiger,
        sync_finance,
        sync_index,
        sync_industry,
        sync_minute,
        sync_money_flow_detail,
        sync_north_flow,
        sync_pool,
        sync_sector,
    )
    from datetime import date as _date

    from app.data import history_backfill as _hist

    def _history_batch(db) -> int:
        _hist.ensure_state(db)
        return int(_hist.run_history_batch(db).get("rows", 0))

    _wrap("daily_k_sync", lambda db: _daily_k(db))
    _wrap("pool_sync", lambda db: sync_pool.sync_pool_snapshot(db))
    _wrap("minute_k_sync", lambda db: sync_minute.sync_one_stock(db, "600519", period=5))
    _wrap("dragon_tiger_sync", lambda db: sync_dragon_tiger.sync_date(db, _date.today().strftime("%Y%m%d")))
    _wrap("north_flow_sync", lambda db: sync_north_flow.sync_all(db))
    _wrap("money_flow_detail_sync", lambda db: sync_money_flow_detail.sync_one_stock(db, "600519"))
    _wrap("sector_sync", lambda db: sync_sector.sync_all(db))
    _wrap("index_sync", lambda db: sync_index.sync_all(db))
    _wrap("industry_sync", lambda db: sync_industry.sync_one_stock(db, "600519"))
    _wrap("sentiment_sync", lambda db: _sentiment(db))
    # V2 agents (BP-V2-009~012): daily report generation
    _wrap("market_agent_sync", lambda db: _run_agent(db, "market"))
    _wrap("review_agent_sync", lambda db: _run_agent(db, "review"))
    # Multi-year history back-fill: one manual batch + a failed-codes reset.
    # One batch ≈ 15 stocks × ~5 chunks ≈ 80s (fits the 300s task deadline).
    _wrap("history_backfill", _history_batch)
    _wrap("history_backfill_reset", lambda db: _hist.reset_failed(db))
    # Finance sync: one capped batch (~350 codes × 0.7s ≈ 4 min, fits the
    # deadline); the nightly scheduler job runs the uncapped version.
    _wrap("finance_sync", lambda db: int(
        sync_finance.sync_all(db, missing_cap=250, stale_cap=100).get("rows", 0)
    ))

    # --- V2.1 数据修复（spec-004）-----------------------------------------
    from app.data import kline_rebuild as _kb
    from app.data import repair_daily

    def _kline_rebuild_batch(db) -> int:
        _kb.ensure_state(db)
        return int(_kb.run_batch(db).get("rows", 0))

    def _repair(db) -> int:
        summary = repair_daily.run_full_repair(db)
        return int(
            summary.get("frozen_repaired", 0) + summary.get("misaligned_repaired", 0)
        )

    _wrap("kline_rebuild_batch", _kline_rebuild_batch)
    _wrap("kline_rebuild_reset", lambda db: _kb.reset_failed(db))
    _wrap("daily_k_repair", _repair)

    # --- V2.1 样本治理与质量巡检 -------------------------------------------
    from app.data import sync_delist, sync_industry_map, sync_trade_status
    from app.services import quality_service

    def _quality_check(db) -> int:
        summary = quality_service.run_daily_check(db)
        return int(summary.get("failed", 0))

    _wrap("quality_check", _quality_check)
    _wrap("trade_status_sync", lambda db: int(
        sync_trade_status.compute_trade_status(db).get("rows", 0)
    ))
    _wrap("trade_status_backfill", lambda db: int(
        sync_trade_status.backfill_trade_status(db).get("rows", 0)
    ))
    def _delist_sync(db) -> int:
        summary = sync_delist.sync_lifecycle(db)
        return int(summary.get("listed", 0)) + int(summary.get("delisted", 0))

    _wrap("delist_sync", _delist_sync)
    _wrap("industry_map_sync", lambda db: int(sync_industry_map.sync_all(db)))

    # V2.1 E3 (BP-V2.1-008, P2): amount/turnover gap backfill over the raw store.
    def _amount_backfill(db) -> int:
        summary = repair_daily.backfill_amount(db, limit_codes=300)
        return int(summary.get("repaired", 0))

    _wrap("amount_backfill", _amount_backfill)

    # --- V2.2 因子健康度监控（BP-V2.2-007 / T2.7）--------------------------
    from app.services import factor_health_service

    def _factor_health(db) -> int:
        summary = factor_health_service.run_factor_health_check(db)
        return int(summary.get("failed", 0))

    _wrap("factor_health_check", _factor_health)


# Tasks that must NOT run inside the 300s synchronous deadline — submitted
# via run_task_async instead (see module docstring).
_LONG_TASKS = {
    "daily_k_repair",
    "kline_rebuild_batch",
    "trade_status_backfill",
    "industry_map_sync",
    "amount_backfill",
}


def _run_agent(db, agent_name: str) -> int:
    """Run a V2 agent; returns 1 on success (agent_service.generate persists)."""
    from app.services import agent_service

    agent_service.generate(db, agent_name, target=None)
    return 1


def _daily_k(db) -> int:
    # delegate to the existing scheduler entry; returns row count best-effort
    from app.scheduler import run_daily_sync

    run_daily_sync()
    return 0


def _sentiment(db) -> int:
    from sqlalchemy import func as _f

    from app.models.stock import DailyPrice
    from app.services import sentiment_service

    # Pick the latest date with settled bars (non-NULL pct_change); a bare max
    # would pick the current in-progress session whose rows have NULL pct_change
    # and yield an all-zero sentiment rollup.
    latest = db.execute(
        select(_f.max(DailyPrice.trade_date)).where(
            DailyPrice.pct_change.is_not(None)
        )
    ).scalar()
    if latest is None:
        return 0
    return 1 if sentiment_service.compute_sentiment(db, latest) is not None else 0


# Human-readable titles for the admin console's task table (the key stays
# visible next to it for logs/API use).
TASK_TITLES = {
    "daily_k_sync": "日K增量同步（全市场，17:30）",
    "pool_sync": "股票池快照同步（16:25）",
    "minute_k_sync": "分钟K同步（示例股）",
    "dragon_tiger_sync": "龙虎榜同步（18:00）",
    "north_flow_sync": "北向资金同步（17:00）",
    "money_flow_detail_sync": "分单资金同步（17:05）",
    "sector_sync": "板块数据同步（17:10）",
    "index_sync": "指数行情同步（16:35）",
    "industry_sync": "行业字段补全（个股页）",
    "sentiment_sync": "市场情绪统计（16:45）",
    "market_agent_sync": "大盘 Agent 日报（18:10）",
    "review_agent_sync": "复盘 Agent 日报（18:20）",
    "history_backfill": "5年历史K线回填（批次）",
    "history_backfill_reset": "历史回填失败重置",
    "finance_sync": "财务数据同步（限量批，19:30）",
    # V2.1
    "kline_rebuild_batch": "raw 日K全量重灌（手动加推一批）",
    "kline_rebuild_reset": "raw 重灌失败重置",
    "daily_k_repair": "脏数据修复（价格冻结/错位段）",
    "quality_check": "数据质量巡检（每日 08:00）",
    "trade_status_sync": "交易状态增量（ST/停牌/涨跌停，19:00）",
    "trade_status_backfill": "交易状态全量回填",
    "delist_sync": "退市名单/生命周期同步（周六 09:00）",
    "industry_map_sync": "行业映射同步（东财→legacy 兜底，周日 09:00）",
    "amount_backfill": "成交额/换手缺失回补",
    # V2.2
    "factor_health_check": "因子健康度周检（IC 衰减/失效预警，周六 09:30）",
}


def list_tasks() -> list[dict]:
    """Static task catalog with the latest run status for each."""
    _register_runners()
    db = SessionLocal()
    try:
        out = []
        for name in _TASK_RUNNERS:
            last = db.execute(
                select(SaAdminTaskLog)
                .where(SaAdminTaskLog.task_name == name)
                .order_by(desc(SaAdminTaskLog.started_at))
                .limit(1)
            ).scalar_one_or_none()
            out.append(
                {
                    "task_name": name,
                    "title": TASK_TITLES.get(name, name),
                    "is_long": name in _LONG_TASKS,
                    "last_status": last.status if last else None,
                    "last_started_at": last.started_at if last else None,
                    "last_finished_at": last.finished_at if last else None,
                    "last_rows": last.rows_affected if last else None,
                }
            )
        return out
    finally:
        db.close()


# Wall-clock cap for a whole task run (seconds). A task may loop over many
# stocks; cap the total so a stuck run can't block the scheduler thread or an
# admin HTTP request indefinitely. Individual akshare fetches are already
# bounded by akshare_client._with_timeout; this is the outer backstop.
_TASK_DEADLINE_SEC: float = 300.0


def _run_with_deadline(runner, task_name: str):
    """Run a task runner under a wall-clock deadline.

    Uses a worker thread + ``future.result(timeout=...)``. On timeout raises
    :class:`TimeoutError`; the (possibly still-running) worker thread is
    abandoned, same trade-off as akshare_client._with_timeout.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    # Dedicated single-worker pool per call so a stuck task can't starve others.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"task-{task_name}") as ex:
        fut = ex.submit(runner)
        try:
            return fut.result(timeout=_TASK_DEADLINE_SEC)
        except FuturesTimeout:
            raise TimeoutError(
                f"task {task_name} exceeded {_TASK_DEADLINE_SEC}s"
            )


def run_task(task_name: str, triggered_by: str) -> dict:
    """Execute a task synchronously and log the outcome.

    :return: the created ``sa_admin_task_log`` row as a dict.
    """
    _register_runners()
    runner = _TASK_RUNNERS.get(task_name)
    if runner is None:
        raise ValueError(f"unknown task: {task_name}")

    started = datetime.now()
    log = SaAdminTaskLog(
        task_name=task_name, started_at=started, status="running", triggered_by=triggered_by
    )
    db = SessionLocal()
    try:
        db.add(log)
        db.commit()
        db.refresh(log)
        log_id = log.id
    finally:
        db.close()

    rows = 0
    status = "success"
    error = None
    try:
        rows = _run_with_deadline(runner, task_name)
    except TimeoutError:
        status = "failed"
        error = f"task exceeded {_TASK_DEADLINE_SEC}s wall-clock"
        logger.warning("admin task %s timed out", task_name)
    except Exception as e:  # noqa: BLE001 - record any failure
        status = "failed"
        error = str(e)
        logger.exception("admin task %s failed", task_name)

    db = SessionLocal()
    try:
        db.execute(
            SaAdminTaskLog.__table__.update()
            .where(SaAdminTaskLog.id == log_id)
            .values(
                finished_at=datetime.now(),
                status=status,
                rows_affected=rows,
                error=error,
            )
        )
        db.commit()
        row = db.execute(
            select(SaAdminTaskLog).where(SaAdminTaskLog.id == log_id)
        ).scalar_one()
        return _log_to_dict(row)
    finally:
        db.close()


# Dedicated single-worker pool for long tasks: serialized so a full-market
# repair can't stack on a re-ingest batch (both hammer the same sources), and
# a stuck task can't starve anything else.
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

_long_task_executor = _ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="admin-long-task"
)


def run_task_async(task_name: str, triggered_by: str) -> int:
    """Submit a long task for background execution; return its log id.

    The task must be listed in :data:`_LONG_TASKS`. The log row is created
    synchronously (status ``running``) so the caller can poll
    :func:`get_run` immediately; completion/failure is written by the worker.
    """
    _register_runners()
    if task_name not in _LONG_TASKS or task_name not in _TASK_RUNNERS:
        raise ValueError(f"not a long task: {task_name}")

    log = SaAdminTaskLog(
        task_name=task_name,
        started_at=datetime.now(),
        status="running",
        triggered_by=triggered_by,
    )
    db = SessionLocal()
    try:
        db.add(log)
        db.commit()
        db.refresh(log)
        log_id = log.id
    finally:
        db.close()

    runner = _TASK_RUNNERS[task_name]

    def _worker() -> None:
        try:
            rows = runner()
            _finalize_run(log_id, status="success", rows=rows)
        except Exception as e:  # noqa: BLE001 - record any failure
            logger.exception("long task %s failed", task_name)
            _finalize_run(log_id, status="failed", error=str(e))

    _long_task_executor.submit(_worker)
    return log_id


def _finalize_run(
    log_id: int, status: str, rows: int | None = None, error: str | None = None
) -> None:
    db = SessionLocal()
    try:
        db.execute(
            SaAdminTaskLog.__table__.update()
            .where(SaAdminTaskLog.id == log_id)
            .values(
                finished_at=datetime.now(),
                status=status,
                rows_affected=rows,
                error=error,
            )
        )
        db.commit()
    finally:
        db.close()


def get_run(run_id: int) -> dict | None:
    """One task-run log row by id (for the async run-status polling endpoint)."""
    db = SessionLocal()
    try:
        row = db.execute(
            select(SaAdminTaskLog).where(SaAdminTaskLog.id == run_id)
        ).scalar_one_or_none()
        return _log_to_dict(row) if row else None
    finally:
        db.close()


def task_logs(task_name: str, limit: int = 20) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(SaAdminTaskLog)
            .where(SaAdminTaskLog.task_name == task_name)
            .order_by(desc(SaAdminTaskLog.started_at))
            .limit(limit)
        ).scalars().all()
        return [_log_to_dict(r) for r in rows]
    finally:
        db.close()


def _log_to_dict(r: SaAdminTaskLog) -> dict:
    result = None
    if r.result_json:
        try:
            result = json.loads(r.result_json)
        except (TypeError, ValueError):
            result = r.result_json
    return {
        "id": r.id,
        "task_name": r.task_name,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "status": r.status,
        "rows_affected": r.rows_affected,
        "progress_done": r.progress_done,
        "progress_total": r.progress_total,
        "result": result,
        "error": r.error,
        "triggered_by": r.triggered_by,
    }


# ---- data sources ----

# The sources the platform ACTUALLY hits (tushare was a V1 PRD idea that
# never got used — removed 2026-09-01). Each entry maps to a real endpoint
# probe in :func:`test_datasource`, not just an import check.
DATASOURCES = [
    {
        "name": "tencent",
        "type": "http",
        "note": "腾讯行情直连：日K主源 proxy.finance.qq.com（含成交额/换手）+ "
                "兜底 web.ifzq.gtimg.cn + 指数/实时行情",
    },
    {
        "name": "eastmoney",
        "type": "akshare",
        "note": "东方财富（经 akshare）：股票池快照 / 日K兜底 / 板块行业 / "
                "龙虎榜 / 北向 / 分单资金 / 分钟K",
    },
    {
        "name": "sina",
        "type": "akshare",
        "note": "新浪财经（经 akshare）：交易日历",
    },
    {
        "name": "sse_szse",
        "type": "akshare",
        "note": "沪深交易所官网（经 akshare）：退市名单（PIT 股票池）",
    },
]


def list_datasources() -> list[dict]:
    """The data-source catalog surfaced in the admin console."""
    return DATASOURCES


def _probe(timeout_sec: float, fn, *args, **kwargs):
    """Run a connectivity probe under a short wall-clock deadline."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="ds-probe") as ex:
        fut = ex.submit(fn, *args, **kwargs)
        try:
            return fut.result(timeout=timeout_sec)
        except FuturesTimeout:
            raise TimeoutError(f"probe exceeded {timeout_sec}s")


def test_datasource(name: str) -> dict:
    """Real endpoint probe per data source (used to be a bare import check).

    Probes are deliberately tiny (1 bar / 1 row) so a manual click is cheap,
    but they DO hit the network — so the result reflects actual reachability
    (e.g. eastmoney's push2 refusing connections shows up as a red result).
    """
    if name == "tencent":
        try:
            from app.data.akshare_client import _TENCENT_KLINE_ALT, _tencent_get

            r = _probe(
                8,
                _tencent_get,
                _TENCENT_KLINE_ALT,
                params={"param": "sh600519,day,,,1,qfq"},
                timeout=6,
            )
            if r is None:
                return {"name": name, "ok": False,
                        "detail": "不可达或处于 WAF 冷却期（501）"}
            code = (r.json() or {}).get("code")
            return {"name": name, "ok": code == 0, "detail": f"kline 探活 code={code}"}
        except Exception as e:  # noqa: BLE001
            return {"name": name, "ok": False, "detail": str(e)[:120]}

    if name == "eastmoney":
        try:
            import akshare as ak

            df = _probe(
                12,
                lambda: ak.stock_zh_a_hist(
                    symbol="600519", period="daily",
                    start_date="20260801", end_date="20260829", adjust="qfq",
                ),
            )
            ok = df is not None and not df.empty
            n = 0 if df is None else len(df)
            return {"name": name, "ok": ok, "detail": f"日K探活返回 {n} 行"
                    + ("" if ok else "（push2 拒连或被限流）")}
        except Exception as e:  # noqa: BLE001
            return {"name": name, "ok": False, "detail": f"不可达：{str(e)[:100]}"}

    if name == "sina":
        try:
            from app.data.akshare_client import fetch_trade_calendar

            cal = _probe(10, fetch_trade_calendar)
            ok = bool(cal)
            return {"name": name, "ok": ok,
                    "detail": f"交易日历 {len(cal)} 天" if ok else "日历为空"}
        except Exception as e:  # noqa: BLE001
            return {"name": name, "ok": False, "detail": f"不可达：{str(e)[:100]}"}

    if name == "sse_szse":
        try:
            import akshare as ak

            df = _probe(10, ak.stock_info_sh_delist)
            ok = df is not None and not df.empty
            n = 0 if df is None else len(df)
            return {"name": name, "ok": ok, "detail": f"沪退市名单 {n} 条"}
        except Exception as e:  # noqa: BLE001
            return {"name": name, "ok": False, "detail": f"不可达：{str(e)[:100]}"}

    return {"name": name, "ok": False, "detail": "unknown data source"}


# ---- user management ----


def list_users(db: Session) -> list[dict]:
    rows = db.execute(select(SaUser).order_by(SaUser.id)).scalars().all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "status": u.status,
            "created_at": u.created_at,
        }
        for u in rows
    ]


def update_user(db: Session, user_id: int, role: str | None = None, status: int | None = None) -> dict | None:
    u = db.get(SaUser, user_id)
    if u is None:
        return None
    if role is not None:
        u.role = role
    if status is not None:
        u.status = status
    db.commit()
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "status": u.status,
    }
