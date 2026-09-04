"""Daily data-quality patrol (V2.1 BP-V2.1-007).

Productizes the step1 validation scripts into a scheduled, threshold-driven
patrol: every check materializes one metric for the latest settled trade
date, compares it against ``sa_data_quality_rule`` thresholds, and upserts a
``sa_data_quality_check`` row (bounded stock-level detail rides in the JSON
column). Scheduled at 08:00; also runnable on demand from admin.

Checks are INCREMENTAL by design (yesterday's fresh rows only, not the
0.5%-tolerance audit over five years of history — that's
``sync_kline.hfq_close_2_close_deviation`` on the rebuild path). Metrics and
their comparison direction:

=========================  ===========================  ==============
check_name / metric        meaning                     direction
=========================  ===========================  ==============
adjustment_break /         rows whose close2close       fail-high
new_row_deviation_count    deviates >0.5% from pct
frozen /                   stocks with a frozen-       fail-high
frozen_stock_count         close segment (120d)
row_baseline /             settled rows vs 15-day       fail-low
row_ratio                  baseline max
field_missing /            % of the day's rows          fail-high
amount_missing_pct         with NULL amount
coverage /                 trade-status coverage        fail-low
trade_status_coverage
coverage /                 industry-mapping coverage    fail-low
industry_coverage
coverage /                 lifecycle coverage           fail-low
lifecycle_coverage
amplitude_anomaly /        rows with intraday range     fail-high
abnormal_rows              >30% of prev close
=========================  ===========================  ==============
"""

import json
import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models.kline import SaAdjustFactor, SaDailyTradeStatus, SaKlineDaily
from app.models.quality import SaDataQualityCheck, SaDataQualityRule
from app.models.stock import DailyPrice, StockPool

logger = logging.getLogger(__name__)

_DEV_TOL = 0.005      # close2close vs pct deviation tolerance
_AMPLITUDE_TOL = 0.30  # intraday range / prev close

_FAIL_LOW = {
    ("row_baseline", "row_ratio"),
    ("coverage", "trade_status_coverage"),
    ("coverage", "industry_coverage"),
    ("coverage", "lifecycle_coverage"),
}


def _active_model():
    from app.core.config import settings
    from app.services.market_service import _kline_model

    return _kline_model()


def _latest_settled(db: Session) -> date | None:
    model = _active_model()
    return db.execute(
        select(func.max(model.trade_date)).where(model.pct_change.is_not(None))
    ).scalar()


def _load_rules(db: Session) -> dict[tuple[str, str], tuple[float | None, float]]:
    rows = db.execute(
        select(
            SaDataQualityRule.check_name,
            SaDataQualityRule.metric_name,
            SaDataQualityRule.warn_threshold,
            SaDataQualityRule.fail_threshold,
        ).where(SaDataQualityRule.enabled == 1)
    ).all()
    return {
        (c, m): (None if w is None else float(w), float(f))
        for c, m, w, f in rows
    }


def _classify(value: float, warn: float | None, fail: float, fail_low: bool) -> str:
    if fail_low:
        if value < fail:
            return "fail"
        if warn is not None and value < warn:
            return "warn"
        return "pass"
    if value > fail:
        return "fail"
    if warn is not None and value > warn:
        return "warn"
    return "pass"


def _save(db: Session, check_date: date, results: list[dict]) -> None:
    for r in results:
        stmt = mysql_insert(SaDataQualityCheck).values(
            check_date=check_date,
            check_name=r["check_name"],
            metric_name=r["metric_name"],
            metric_value=None if r["value"] is None else round(r["value"], 4),
            status=r["status"],
            detail=json.dumps(r.get("detail"), ensure_ascii=False, default=str)[:60000],
        )
        stmt = stmt.on_duplicate_key_update(
            metric_value=stmt.inserted.metric_value,
            status=stmt.inserted.status,
            detail=stmt.inserted.detail,
        )
        db.execute(stmt)
    db.commit()


# ---------------------------------------------------------------------------
# Individual checks → (value, detail)
# ---------------------------------------------------------------------------

def _check_adjustment_break(db: Session, d: date) -> tuple[float | None, dict]:
    model = _active_model()
    from app.core.config import settings

    cur = {
        code: (close, pct)
        for code, close, pct in db.execute(
            select(model.stock_code, model.close, model.pct_change).where(
                model.trade_date == d, model.pct_change.is_not(None)
            )
        ).all()
    }
    # Per code: the latest trade date strictly before d (almost always the
    # previous session — so the group-by yields 1-3 distinct dates, and the
    # prev-close fetch below stays a couple of bulk queries).
    max_dates = dict(
        db.execute(
            select(model.stock_code, func.max(model.trade_date)).where(
                model.stock_code.in_(list(cur.keys())), model.trade_date < d
            ).group_by(model.stock_code)
        ).all()
    )
    prev_close: dict[str, float] = {}
    by_date: dict[date, list[str]] = {}
    for code, md in max_dates.items():
        by_date.setdefault(md, []).append(code)
    for md, codes in by_date.items():
        for code, close in db.execute(
            select(model.stock_code, model.close).where(
                model.stock_code.in_(codes), model.trade_date == md
            )
        ).all():
            if close is not None:
                prev_close[code] = float(close)

    # On the v2 store, close2close must run on the hfq basis (raw × factor),
    # so the day's factor ratio re-bases the comparison.
    factor_ratio: dict[str, float] = {}
    if settings.kline_source == "v2" and max_dates:
        pairs = db.execute(
            select(
                SaAdjustFactor.stock_code,
                SaAdjustFactor.trade_date,
                SaAdjustFactor.adj_factor,
            ).where(
                SaAdjustFactor.stock_code.in_(list(max_dates.keys())),
                SaAdjustFactor.trade_date.in_([d] + list(set(max_dates.values()))),
            )
        ).all()
        f_by = {(c, dt): float(f) for c, dt, f in pairs}
        for code, md in max_dates.items():
            f_cur, f_prev = f_by.get((code, d)), f_by.get((code, md))
            if f_cur and f_prev:
                factor_ratio[code] = f_cur / f_prev

    bad: list[dict] = []
    comparable = 0
    for code, (close, pct) in cur.items():
        if code not in prev_close or close is None or pct is None:
            continue
        comparable += 1
        ratio = factor_ratio.get(code, 1.0)
        ret_calc = (float(close) * ratio) / prev_close[code] - 1.0
        ret_true = float(pct) / 100.0
        if abs(ret_calc - ret_true) > _DEV_TOL:
            bad.append(
                {"code": code, "deviation": round(abs(ret_calc - ret_true), 6)}
            )
    bad.sort(key=lambda x: -x["deviation"])
    return float(len(bad)), {
        "comparable": comparable,
        "offenders": bad[:50],
    }


def _check_row_baseline(db: Session, d: date) -> tuple[float | None, dict]:
    from app.data.backfill import settled_counts

    counts = settled_counts(db)
    today_count = counts.get(d, 0)
    prior = [c for dd, c in counts.items() if dd < d]
    if not prior or not max(prior):
        return None, {"note": "no baseline"}
    baseline = max(prior)
    return today_count / baseline, {"today": today_count, "baseline": baseline}


def _check_field_missing(db: Session, d: date) -> tuple[float | None, dict]:
    model = _active_model()
    total = db.execute(
        select(func.count()).select_from(model).where(model.trade_date == d)
    ).scalar() or 0
    if not total:
        return None, {"note": "no rows"}
    missing = db.execute(
        select(func.count()).select_from(model).where(
            model.trade_date == d, model.amount.is_(None)
        )
    ).scalar() or 0
    return missing / total * 100.0, {"missing": missing, "total": total}


def _check_amplitude(db: Session, d: date) -> tuple[float | None, dict]:
    model = _active_model()
    rows = db.execute(
        select(model.stock_code, model.high, model.low, model.close).where(
            model.trade_date == d
        )
    ).all()
    codes = [r[0] for r in rows]
    prev = {}
    if codes:
        max_dates = dict(
            db.execute(
                select(model.stock_code, func.max(model.trade_date)).where(
                    model.stock_code.in_(codes), model.trade_date < d
                ).group_by(model.stock_code)
            ).all()
        )
        by_date: dict[date, list[str]] = {}
        for code, md in max_dates.items():
            by_date.setdefault(md, []).append(code)
        for md, md_codes in by_date.items():
            for code, pc in db.execute(
                select(model.stock_code, model.close).where(
                    model.stock_code.in_(md_codes), model.trade_date == md
                )
            ).all():
                if pc is not None:
                    prev[code] = float(pc)
    bad = []
    for code, high, low, _close in rows:
        if code in prev and prev[code] and high is not None and low is not None:
            if (float(high) - float(low)) / prev[code] > _AMPLITUDE_TOL:
                bad.append({"code": code})
    return float(len(bad)), {"offenders": bad[:50]}


def _check_frozen(db: Session, d: date) -> tuple[float | None, dict]:
    from app.data.repair_daily import find_frozen

    findings = find_frozen(db, lookback_days=120)
    offenders = [
        {"code": code, "segments": segs[:3]}
        for code, segs in sorted(findings.items(), key=lambda kv: -sum(s["bars"] for s in kv[1]))
    ][:50]
    return float(len(findings)), {"offenders": offenders}


def _check_coverage(db: Session, d: date) -> dict[tuple[str, str], tuple[float | None, dict]]:
    from app.data import sync_delist, sync_industry_map, sync_trade_status

    ts = sync_trade_status.status_coverage(db, d)
    ind = sync_industry_map.coverage(db)
    lc = sync_delist.lifecycle_coverage(db)
    return {
        ("coverage", "trade_status_coverage"): (ts, {"asof": str(d)}),
        ("coverage", "industry_coverage"): (ind, {"latest_snapshot": True}),
        ("coverage", "lifecycle_coverage"): (lc, {}),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_daily_check(db: Session, trade_date: date | None = None) -> dict:
    """Run every enabled check for ``trade_date`` (default: latest settled).

    :return: summary ``{"date": d, "results": [...], "failed": n}``.
    """
    d = trade_date or _latest_settled(db)
    if d is None:
        return {"date": None, "results": [], "failed": 0}

    rules = _load_rules(db)
    metrics: dict[tuple[str, str], tuple[float | None, dict]] = {}

    try:
        metrics[("adjustment_break", "new_row_deviation_count")] = _check_adjustment_break(db, d)
    except Exception as e:  # noqa: BLE001 - one broken check must not kill the patrol
        logger.exception("adjustment_break check failed")
        metrics[("adjustment_break", "new_row_deviation_count")] = (None, {"error": str(e)})
    for name, fn in (
        ("row_baseline", _check_row_baseline),
        ("field_missing", _check_field_missing),
        ("amplitude_anomaly", _check_amplitude),
        ("frozen", _check_frozen),
    ):
        metric = "row_ratio" if name == "row_baseline" else (
            "amount_missing_pct" if name == "field_missing"
            else "frozen_stock_count" if name == "frozen"
            else "abnormal_rows"
        )
        try:
            metrics[(name, metric)] = fn(db, d)
        except Exception as e:  # noqa: BLE001
            logger.exception("%s check failed", name)
            metrics[(name, metric)] = (None, {"error": str(e)})
    try:
        metrics.update(_check_coverage(db, d))
    except Exception as e:  # noqa: BLE001
        logger.exception("coverage checks failed")

    results = []
    for (check, metric), (value, detail) in metrics.items():
        rule = rules.get((check, metric))
        if rule is None:
            continue
        warn, fail = rule
        status = (
            "fail" if value is None and detail.get("error")
            else _classify(value, warn, fail, fail_low=(check, metric) in _FAIL_LOW)
        ) if value is not None else "warn"
        results.append(
            {
                "check_name": check,
                "metric_name": metric,
                "value": value,
                "status": status,
                "detail": detail,
            }
        )
    _save(db, d, results)
    failed = sum(1 for r in results if r["status"] == "fail")
    if failed:
        logger.error("quality patrol %s: %d checks FAILED", d, failed)
    return {"date": str(d), "results": results, "failed": failed}


# ---------------------------------------------------------------------------
# Query side (admin API)
# ---------------------------------------------------------------------------


def daily_report(db: Session, check_date: date | None = None) -> list[dict]:
    """Report rows for ``check_date``; falls back to the latest date with rows.

    The panel defaults to *today*, but the patrol materializes rows for the
    latest SETTLED trade date — so an explicit empty date (weekend, before
    08:00, first visit) transparently shows the most recent report instead of
    a bare empty state. Each row carries its own ``check_date``.
    """
    if check_date is not None:
        rows = db.execute(
            select(SaDataQualityCheck).where(SaDataQualityCheck.check_date == check_date)
        ).scalars().all()
        if rows:
            return [_row_to_dict(r) for r in rows]
    latest = db.execute(select(func.max(SaDataQualityCheck.check_date))).scalar()
    if latest is None:
        return []
    rows = db.execute(
        select(SaDataQualityCheck).where(SaDataQualityCheck.check_date == latest)
    ).scalars().all()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(r: SaDataQualityCheck) -> dict:
    return {
        "check_date": str(r.check_date),
        "check_name": r.check_name,
        "metric_name": r.metric_name,
        "metric_value": float(r.metric_value) if r.metric_value is not None else None,
        "status": r.status,
        "detail": r.detail,
    }


def trend(db: Session, days: int = 30) -> list[dict]:
    rows = db.execute(
        select(SaDataQualityCheck)
        .order_by(SaDataQualityCheck.check_date.desc())
        .limit(days * 12)
    ).scalars().all()
    out = [
        {
            "check_date": str(r.check_date),
            "check_name": r.check_name,
            "metric_name": r.metric_name,
            "metric_value": float(r.metric_value) if r.metric_value is not None else None,
            "status": r.status,
        }
        for r in rows
    ]
    return out
