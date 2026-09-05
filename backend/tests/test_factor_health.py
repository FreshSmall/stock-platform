"""Tests for factor health monitoring (V2.2 T2.7 / BP-V2.2-007).

The threshold/status logic is pure enough to test on Decimal values; the DB
tests run a real patrol on the preset factors (persist_ic=False keeps it
fast) and verify persistence + report shape + idempotent rerun.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models.quality import SaDataQualityCheck, SaDataQualityRule
from app.services import factor_health_service as fh


def test_status_threshold_bands():
    # ic_ir: thresholds apply to |value|, higher is healthier
    assert fh._status(0.5, Decimal("0.10"), Decimal("0.05"), band=False) == "pass"
    assert fh._status(-0.3, Decimal("0.10"), Decimal("0.05"), band=False) == "pass"
    assert fh._status(-0.07, Decimal("0.10"), Decimal("0.05"), band=False) == "warn"
    assert fh._status(0.01, Decimal("0.10"), Decimal("0.05"), band=False) == "fail"
    assert fh._status(None, Decimal("0.10"), Decimal("0.05"), band=False) == "warn"
    # band metrics (ic_decay / recent_ic): 0 is healthy, extremes are not
    assert fh._status(0.01, Decimal("0.05"), Decimal("0.10"), band=True) == "pass"
    assert fh._status(-0.07, Decimal("0.05"), Decimal("0.10"), band=True) == "warn"
    assert fh._status(0.20, Decimal("0.05"), Decimal("0.10"), band=True) == "fail"


def test_recent_mean_ic_filters_by_cutoff():
    today = date.today()
    series = [
        {"trade_date": (today - timedelta(days=10)).isoformat(), "ic": -0.10},
        {"trade_date": (today - timedelta(days=30)).isoformat(), "ic": -0.06},
        {"trade_date": (today - timedelta(days=120)).isoformat(), "ic": 0.30},
        {"trade_date": (today - timedelta(days=40)).isoformat(), "ic": None},
    ]
    # only the first two rows are inside the 90-day window and non-null
    assert fh._recent_mean_ic(series, days=90) == round((-0.10 - 0.06) / 2, 4)
    assert fh._recent_mean_ic([], days=90) is None


def test_patrol_persists_and_is_idempotent(db_session):
    first = fh.run_factor_health_check(db_session, persist_ic=False)
    assert first["checked"] == len(fh.FACTOR_CODES)
    assert first["rows"] == first["checked"] * 3

    def _count():
        return len(
            db_session.execute(
                select(SaDataQualityCheck).where(
                    SaDataQualityCheck.check_name == fh.CHECK_NAME
                )
            )
            .scalars()
            .all()
        )

    n1 = _count()
    second = fh.run_factor_health_check(db_session, persist_ic=False)
    assert _count() == n1 == first["rows"]  # rerun replaces, not appends
    assert second["checked"] == first["checked"]

    rows = (
        db_session.execute(
            select(SaDataQualityCheck).where(
                SaDataQualityCheck.check_name == fh.CHECK_NAME
            )
        )
        .scalars()
        .all()
    )
    assert all(r.status in ("pass", "warn", "fail") for r in rows)
    # factor encoded in metric_name ("<metric>:<code>") to fit the table UK
    assert all(":" in r.metric_name for r in rows)


def test_rules_seeded_on_first_run(db_session):
    rules = (
        db_session.execute(
            select(SaDataQualityRule).where(
                SaDataQualityRule.check_name == fh.CHECK_NAME
            )
        )
        .scalars()
        .all()
    )
    assert {r.metric_name for r in rules} == {"ic_ir", "ic_decay", "recent_ic"}


def test_health_report_shape(db_session):
    report = fh.health_report(db_session)
    assert report["as_of"] is not None
    assert sorted(f["factor_code"] for f in report["factors"]) == sorted(
        fh.FACTOR_CODES
    )
    for f in report["factors"]:
        assert f["worst"] in ("pass", "warn", "fail")
        assert set(f["metrics"]) == {"ic_ir", "ic_decay", "recent_ic"}
