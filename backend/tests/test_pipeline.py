"""Tests for the V2.5 pipeline topology, state machine and bookkeeping hooks.

DB-backed (real ``stock_analysis``). The bookkeeping hooks open their own
sessions (production behaviour), so assertions re-read through the fixture
session after a commit — MySQL's REPEATABLE READ snapshot would otherwise
hide the rows the hooks wrote. Every test cleans up the run/step/alert rows
it created for today.
"""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.pipeline import SaPipelineRun, SaPipelineStep
from app.models.quality import SaDataQualityCheck, SaDataQualityRule
from app.models.user import SaUser
from app.services import pipeline_service as ps
from app.services.pipeline_service import (
    PIPELINE_TOPOLOGY,
    TASK_ALIASES,
    recompute_run_status,
)

client = TestClient(app)

_TODAY = date.today()


@pytest.fixture
def admin_token(db_session):
    """A throwaway admin JWT (the conftest auth fixtures are normal users)."""
    import uuid

    from app.core.security import create_access_token
    from app.services import user_service

    uname = f"pipeadm_{uuid.uuid4().hex[:8]}"
    u = user_service.register(db_session, uname, "pw-test-123")
    u.role = "admin"
    db_session.commit()
    uid = u.id
    try:
        yield create_access_token(uid)
    finally:
        db_session.query(SaUser).filter_by(id=uid).delete()
        db_session.commit()


def _cleanup_today_rows(db) -> None:
    """Remove all of today's pipeline bookkeeping rows.

    Also runs BEFORE each hook test: other suites trigger real topology tasks
    (e.g. test_admin_api's sentiment run) whose hooks create today's run, and
    assertions like "no run exists" or "attempts == 2" must not see them.
    """
    db.rollback()
    runs = db.query(SaPipelineRun).filter_by(run_date=_TODAY).all()
    for r in runs:
        db.query(SaPipelineStep).filter_by(run_id=r.id).delete()
        db.delete(r)
    db.query(SaDataQualityCheck).filter(
        SaDataQualityCheck.check_date == _TODAY,
        SaDataQualityCheck.check_name == "pipeline_health",
    ).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def pipeline_db(db_session):
    """Run the hooks against the real DB inside a clean today-slate."""
    _cleanup_today_rows(db_session)
    yield db_session
    _cleanup_today_rows(db_session)


def _run_of(db, pipeline: str = "daily") -> SaPipelineRun | None:
    # rollback() (not commit()) — a read-only session commit doesn't refresh
    # the REPEATABLE READ snapshot, so hook-committed rows would stay invisible
    db.rollback()
    return (
        db.query(SaPipelineRun)
        .filter_by(run_date=_TODAY, pipeline_type=pipeline)
        .one_or_none()
    )


def _steps_of(db, run_id: int) -> dict[str, SaPipelineStep]:
    db.rollback()
    rows = db.query(SaPipelineStep).filter_by(run_id=run_id).all()
    return {s.step_key: s for s in rows}


# ---------------------------------------------------------------------------
# Pure state machine
# ---------------------------------------------------------------------------


def test_recompute_all_success_final():
    states = [("success", False), ("success", True), ("skipped", False)]
    assert recompute_run_status(states, final=True) == "success"


def test_recompute_non_critical_failure_is_partial():
    states = [("success", False), ("failed", False), ("success", True)]
    assert recompute_run_status(states, final=True) == "partial"


def test_recompute_critical_failure_is_failed_even_midway():
    states = [("success", False), ("failed", True)]
    assert recompute_run_status(states, final=False) == "failed"
    assert recompute_run_status(states, final=True) == "failed"


def test_recompute_running_until_final():
    states = [("success", False)]
    assert recompute_run_status(states, final=False) == "running"
    assert recompute_run_status(states, final=True) == "success"


def test_recompute_non_terminal_steps_keep_running():
    states = [("success", False), ("running", False)]
    assert recompute_run_status(states, final=True) == "running"


# ---------------------------------------------------------------------------
# Topology consistency (scheduler manifest == topology)
# ---------------------------------------------------------------------------


def test_cron_specs_match_topology():
    from app.scheduler import _cron_job_specs
    from app.services.pipeline_service import NON_TOPOLOGY_CRON_JOBS

    specs = _cron_job_specs()
    ids = {s["id"] for s in specs}
    topo_keys = {s.step_key for s in PIPELINE_TOPOLOGY}
    # job ids = topology steps + the 23:00 compensation + the 08:00 patrol;
    # the finance alias appears as task_name only (job id stays finance_sync)
    assert ids == topo_keys | {"daily_k_sync_retry"} | NON_TOPOLOGY_CRON_JOBS
    # every executed task is a topology step (directly or via alias);
    # the 08:00 patrol is the only explicitly exempt cron task
    for s in specs:
        if s["task_name"] in NON_TOPOLOGY_CRON_JOBS:
            continue
        assert ps.step_spec(s["task_name"]) is not None


def test_topology_shape_invariants():
    keys = [s.step_key for s in PIPELINE_TOPOLOGY]
    assert len(keys) == len(set(keys)), "duplicate step_key"
    # daily pipeline closes with paper_tick — the run-finalization anchor
    daily = [s.step_key for s in PIPELINE_TOPOLOGY if s.pipeline == "daily"]
    assert daily[-1] == "paper_tick"
    assert "daily_k_sync" in daily and "finance_sync" in daily
    # aliases point at real steps
    for alias, target in TASK_ALIASES.items():
        assert target in keys
        assert ps.step_spec(alias).step_key == target


def test_quality_check_disabled_leaves_specs(monkeypatch):
    from app.core.config import settings
    from app.scheduler import _cron_job_specs

    monkeypatch.setattr(settings, "quality_check_enabled", True)
    assert "quality_check" in {s["id"] for s in _cron_job_specs()}
    monkeypatch.setattr(settings, "quality_check_enabled", False)
    assert "quality_check" not in {s["id"] for s in _cron_job_specs()}


# ---------------------------------------------------------------------------
# Bookkeeping lifecycle (real DB)
# ---------------------------------------------------------------------------


def test_step_lifecycle_success_run(pipeline_db):
    with patch("app.scheduler._is_trade_day", return_value=True):
        ps.on_step_start("pool_sync", None, "scheduler")
        run = _run_of(pipeline_db)
        assert run is not None and run.status == "running"
        step = _steps_of(pipeline_db, run.id)["pool_sync"]
        assert step.status == "running" and step.attempts == 1

        ps.on_step_finish("pool_sync", None, "success", None, "scheduler")
        step = _steps_of(pipeline_db, run.id)["pool_sync"]
        assert step.status == "success" and step.duration_ms is not None

        # run stays running until the LAST daily step (paper_tick) finishes
        assert _run_of(pipeline_db).status == "running"
        ps.on_step_start("paper_tick", None, "scheduler")
        ps.on_step_finish("paper_tick", None, "success", None, "scheduler")
        assert _run_of(pipeline_db).status == "success"


def test_step_lifecycle_critical_failure_fails_run(pipeline_db):
    with patch("app.scheduler._is_trade_day", return_value=True):
        ps.on_step_start("pool_sync", None, "scheduler")
        ps.on_step_finish("pool_sync", None, "success", None, "scheduler")
        ps.on_step_start("daily_k_sync", None, "scheduler")
        ps.on_step_finish("daily_k_sync", None, "failed", "boom", "manual:test")
        assert _run_of(pipeline_db).status == "failed"
        step = _steps_of(pipeline_db, _run_of(pipeline_db).id)["daily_k_sync"]
        assert step.status == "failed" and step.error == "boom"


def test_attempts_increment_and_alias_books_same_step(pipeline_db):
    with patch("app.scheduler._is_trade_day", return_value=True):
        ps.on_step_start("daily_k_sync", 111, "scheduler")
        ps.on_step_finish("daily_k_sync", 111, "failed", "partial", "scheduler")
        # 23:00 compensation (alias) merges into the SAME step row
        ps.on_step_start("daily_k_sync_retry", 222, "scheduler")
        ps.on_step_finish("daily_k_sync_retry", 222, "success", None, "scheduler")
        steps = _steps_of(pipeline_db, _run_of(pipeline_db).id)
        assert set(steps) == {"daily_k_sync"}, "alias must not create a new step"
        assert steps["daily_k_sync"].attempts == 2
        assert steps["daily_k_sync"].status == "success"
        assert steps["daily_k_sync"].task_log_id == 222


def test_non_trading_day_skips_bookkeeping(pipeline_db):
    with patch("app.scheduler._is_trade_day", return_value=False):
        ps.on_step_start("pool_sync", None, "scheduler")
        ps.on_step_finish("pool_sync", None, "success", None, "scheduler")
    assert _run_of(pipeline_db) is None


def test_pipeline_enabled_false_disables_hooks(pipeline_db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "pipeline_enabled", False)
    with patch("app.scheduler._is_trade_day", return_value=True):
        ps.on_step_start("pool_sync", None, "scheduler")
        ps.on_step_finish("pool_sync", None, "success", None, "scheduler")
    assert _run_of(pipeline_db) is None


# ---------------------------------------------------------------------------
# Retry exhaustion → alert
# ---------------------------------------------------------------------------


def _alert_rows(db):
    db.rollback()
    return (
        db.query(SaDataQualityCheck)
        .filter_by(check_date=_TODAY, check_name="pipeline_health")
        .all()
    )


def test_retry_exhaustion_writes_alert(pipeline_db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "pipeline_step_retry_max", 1)
    with patch("app.scheduler._is_trade_day", return_value=True):
        # attempt 1 fails on the scheduler path → retry pending, no alert
        ps.on_step_start("sector_sync", None, "scheduler")
        ps.on_step_finish("sector_sync", None, "failed", "waf", "scheduler")
        assert _alert_rows(pipeline_db) == []

        # attempt 2 exceeds retry_max=1 → terminal failure → alert
        ps.on_step_start("sector_sync", None, "scheduler:retry")
        ps.on_step_finish("sector_sync", None, "failed", "waf again", "scheduler:retry")
        rows = _alert_rows(pipeline_db)
        assert len(rows) == 1
        assert rows[0].metric_name == "step_failed:sector_sync"
        assert rows[0].status == "fail"


def test_manual_failure_alerts_immediately(pipeline_db):
    with patch("app.scheduler._is_trade_day", return_value=True):
        ps.on_step_start("sector_sync", None, "manual:test")
        ps.on_step_finish("sector_sync", None, "failed", "boom", "manual:test")
    rows = _alert_rows(pipeline_db)
    assert len(rows) == 1 and rows[0].metric_name == "step_failed:sector_sync"


def test_retry_between_attempts_recovers_without_alert(pipeline_db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "pipeline_step_retry_max", 1)
    with patch("app.scheduler._is_trade_day", return_value=True):
        ps.on_step_start("sector_sync", None, "scheduler")
        ps.on_step_finish("sector_sync", None, "failed", "hiccup", "scheduler")
        ps.on_step_start("sector_sync", None, "scheduler:retry")
        ps.on_step_finish("sector_sync", None, "success", None, "scheduler:retry")
    assert _alert_rows(pipeline_db) == []
    run = _run_of(pipeline_db)
    assert _steps_of(pipeline_db, run.id)["sector_sync"].attempts == 2


# ---------------------------------------------------------------------------
# Rules seeding + patrol
# ---------------------------------------------------------------------------


def test_ensure_rules_idempotent(pipeline_db):
    ps.ensure_rules(pipeline_db)
    ps.ensure_rules(pipeline_db)  # second call must not duplicate
    rows = (
        pipeline_db.query(SaDataQualityRule)
        .filter_by(check_name="pipeline_health")
        .all()
    )
    names = {r.metric_name for r in rows}
    assert {"step_failed", "step_missing"} <= names
    # clean up only rows this test seeded (production seeds them on first patrol)
    for r in rows:
        if r.metric_name in ("step_failed", "step_missing"):
            pipeline_db.delete(r)
    pipeline_db.commit()


def test_patrol_missing_counts_gaps_and_refreshes_failed(pipeline_db):
    with patch("app.scheduler._is_trade_day", return_value=True):
        ps.on_step_start("pool_sync", None, "scheduler")
        ps.on_step_finish("pool_sync", None, "success", None, "scheduler")
        ps.on_step_start("index_sync", None, "scheduler")
        ps.on_step_finish("index_sync", None, "failed", "down", "manual:test")

    value, detail = ps.patrol_missing(pipeline_db, _TODAY)
    assert value is not None
    daily_keys = {s.step_key for s in PIPELINE_TOPOLOGY if s.pipeline == "daily"}
    missing_steps = {m["step"] for m in detail["missing"]}
    # pool_sync succeeded → not missing; index_sync failed → missing (non-success)
    assert "pool_sync" not in missing_steps
    assert "index_sync" in missing_steps
    assert missing_steps & daily_keys  # the never-fired steps are all missing
    # the failed step got its alert row refreshed by the patrol
    metrics = {r.metric_name for r in _alert_rows(pipeline_db)}
    assert "step_failed:index_sync" in metrics


def test_patrol_missing_none_when_not_live(db_session):
    # only true when no runs exist at all; skip if another suite left rows
    if db_session.query(SaPipelineRun).count() > 0:
        pytest.skip("pipeline runs already exist (other suite)")
    value, _ = ps.patrol_missing(db_session, _TODAY)
    assert value is None


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


def test_pipeline_endpoints_require_admin(db_session):
    u = None
    try:
        import uuid

        from app.core.security import create_access_token
        from app.services import user_service

        uname = f"pipeusr_{uuid.uuid4().hex[:8]}"
        u = user_service.register(db_session, uname, "pw-test-123")
        db_session.commit()
        headers = {"Authorization": f"Bearer {create_access_token(u.id)}"}
        for url in ["/api/v1/admin/pipeline/daily", "/api/v1/admin/pipeline/summary"]:
            assert client.get(url, headers=headers).status_code == 403
    finally:
        if u is not None:
            db_session.query(SaUser).filter_by(id=u.id).delete()
            db_session.commit()


def test_pipeline_daily_and_summary_shape(admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}

    resp = client.get("/api/v1/admin/pipeline/daily", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data) >= {"run", "steps", "long_tasks"}

    resp = client.get("/api/v1/admin/pipeline/summary", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json()["data"], list)
