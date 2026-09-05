"""Factor health monitoring (V2.2 T2.7 / BP-V2.2-007).

Weekly IC health patrol over the preset factors, answering one question per
factor: **is the effect that justified its inclusion still alive?**

Design:

- Reuses the ``sa_data_quality_rule`` / ``sa_data_quality_check`` tables with
  ``check_name='factor_health'`` — a health metric is structurally the same
  thing as a quality metric (value × thresholds × status × detail), so no new
  tables and the admin quality page's model carries over. The check table's
  unique key is (date, check_name, metric_name), so each factor×metric row
  encodes the factor in ``metric_name`` as ``"<metric>:<factor_code>"``;
  threshold rules stay on the bare metric names.
- Evidence comes from :func:`factor_service.compute_ic_series` (the same
  service the research page uses — one source of truth), which already
  persists per-date RankIC rows into ``sa_factor_ic``.
- Three metrics per factor:
    * ``ic_ir``        — ICIR over the trailing window (signal-to-noise);
    * ``ic_decay``     — mean_IC(h=20) − mean_IC(|ICIR| ranking horizon, 10);
      a reversal factor that loses steam at long horizons shows up here;
    * ``recent_ic``    — mean IC over the freshest quarter (regime shift alarm;
      the roc20-2021 lesson: effects die, sometimes abruptly).
- Alerting is threshold-based like every quality check: outside the band →
  ``warn``/``fail`` status on the row; the Admin quality tab already renders
  pass/warn/fail rows, so the failure path needs zero new UI plumbing.
- Run weekly (Sat 09:30, after delist_sync) and on demand via admin/API;
  reruns upsert (replace) the week's rows — idempotent like quality patrol.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.factor.multi_factor import PRESET_V2_REVERSAL
from app.models.kline import SaIndustryMap  # noqa: F401  (import cost only)
from app.models.quality import SaDataQualityCheck, SaDataQualityRule
from app.services import factor_service

logger = logging.getLogger(__name__)

CHECK_NAME = "factor_health"

# Default thresholds (stored as rules on first run, editable via the
# sa_data_quality_rule table like every other quality check):
# - ic_ir: |ICIR| below WARN means the factor is noise-ish at this horizon;
#   below FAIL it is effectively dead. ICIR is signed, thresholds apply to
#   |ICIR| (a strong negative IC is healthy for a reversal factor).
# - ic_decay: beyond ±band the h=10 → h=20 drift is flagged for review.
# - recent_ic: trailing-quarter mean IC that flips sign vs the window mean
#   beyond the band is the regime-shift alarm.
DEFAULT_RULES = [
    ("ic_ir", Decimal("0.10"), Decimal("0.05")),      # warn, fail (|ICIR|)
    ("ic_decay", Decimal("0.05"), Decimal("0.10")),   # ±band warn / fail
    ("recent_ic", Decimal("0.03"), Decimal("0.06")),  # ±band warn / fail
]

# Preset factors with their expected IC sign (from the 2026-08 survey):
# health logic treats a reversal factor's "strong" direction as |value|.
FACTOR_CODES = [spec.code for spec in PRESET_V2_REVERSAL]

# Trailing evidence window for the weekly patrol.
WINDOW_DAYS = 365
HORIZONS = (10, 20)
# Weekly cadence doesn't need a 5-day rebalance grid; step 10 halves the
# patrol runtime (~1 min → ~30s) with no material evidence loss.
STEP = 10


def _ensure_rules(db: Session) -> dict[str, tuple[Decimal, Decimal]]:
    """Load threshold rules, seeding defaults on first run."""
    rows = db.execute(
        select(SaDataQualityRule).where(SaDataQualityRule.check_name == CHECK_NAME)
    ).scalars().all()
    if rows:
        return {r.metric_name: (r.warn_threshold, r.fail_threshold) for r in rows}
    for metric, warn, fail in DEFAULT_RULES:
        db.add(
            SaDataQualityRule(
                check_name=CHECK_NAME,
                metric_name=metric,
                warn_threshold=warn,
                fail_threshold=fail,
                enabled=1,
            )
        )
    db.commit()
    logger.info("factor_health: seeded %d default rules", len(DEFAULT_RULES))
    return {m: (w, f) for m, w, f in DEFAULT_RULES}


def _status(
    value: float | None, warn: Decimal, fail: Decimal, band: bool
) -> str:
    """pass / warn / fail against (|.| for ic_ir, ±band for the others)."""
    if value is None:
        return "warn"  # missing evidence is itself worth a look
    v = abs(value) if band is False else value
    lo, hi = -float(fail), float(fail)
    wlo, whi = -float(warn), float(warn)
    if band:
        if v < wlo or v > whi:
            return "fail" if v < lo or v > hi else "warn"
        return "pass"
    if v < float(fail):
        return "fail"
    if v < float(warn):
        return "warn"
    return "pass"


def run_factor_health_check(db: Session, persist_ic: bool = True) -> dict:
    """Weekly patrol: compute, threshold, upsert health rows.

    :return: ``{"checked": n_factors, "rows": n_rows, "alerted": n_alerts,
        "as_of": ...}`` — matches the shape quality_service returns.
    """
    rules = _ensure_rules(db)
    end = date.today()
    start = end - timedelta(days=WINDOW_DAYS)

    # rerun = replace this run's rows (same idempotency contract as quality)
    db.execute(delete(SaDataQualityCheck).where(SaDataQualityCheck.check_name == CHECK_NAME))

    rows: list[SaDataQualityCheck] = []
    summary: list[dict] = []
    for code in FACTOR_CODES:
        try:
            res = factor_service.compute_ic_series(
                db, code, start, end,
                horizons=HORIZONS, step=STEP, pool="pit",
                universe_size=300, persist=persist_ic,
            )
        except ValueError as e:
            logger.warning("factor_health %s: %s", code, e)
            continue
        if not res:
            logger.warning("factor_health %s: no IC evidence in window", code)
            continue

        s10 = res["summary"].get("10", {})
        s20 = res["summary"].get("20", {})
        icir = s10.get("icir")
        mean10, mean20 = s10.get("mean_ic"), s20.get("mean_ic")
        decay = (
            round(mean20 - mean10, 4)
            if mean10 is not None and mean20 is not None
            else None
        )
        # freshest-quarter mean IC from the per-date series
        recent = _recent_mean_ic(res["series"], days=90)

        metrics = {
            "ic_ir": icir,
            "ic_decay": decay,
            "recent_ic": recent,
        }
        for metric, value in metrics.items():
            warn, fail = rules[metric]
            status = _status(value, warn, fail, band=(metric != "ic_ir"))
            rows.append(
                SaDataQualityCheck(
                    check_date=end,
                    check_name=CHECK_NAME,
                    # factor rides in metric_name: the table's UK is
                    # (date, check, metric) with no factor column
                    metric_name=f"{metric}:{code}",
                    metric_value=None if value is None else Decimal(str(round(value, 4))),
                    status=status,
                    detail={"factor_code": code},
                )
            )
        summary.append(
            {
                "factor_code": code,
                "icir": icir,
                "mean_ic_10": mean10,
                "mean_ic_20": mean20,
                "ic_decay": decay,
                "recent_ic": recent,
                "statuses": {
                    m: _status(v, *rules[m], band=(m != "ic_ir"))
                    for m, v in metrics.items()
                },
            }
        )

    db.add_all(rows)
    db.commit()
    alerted = sum(1 for r in rows if r.status != "pass")
    return {
        "checked": len(summary),
        "rows": len(rows),
        "failed": alerted,
        "as_of": end.isoformat(),
        "factors": summary,
    }


def _recent_mean_ic(series: list[dict], days: int) -> float | None:
    """Mean IC over the trailing ``days`` of the ic-series payload."""
    if not series:
        return None
    cutoff = date.today() - timedelta(days=days)
    vals = [
        r["ic"]
        for r in series
        if r["ic"] is not None
        and date.fromisoformat(r["trade_date"]) >= cutoff
    ]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def health_report(db: Session, limit_days: int = 90) -> dict:
    """Latest patrol results for the Admin tab (grouped by factor × metric)."""
    since = date.today() - timedelta(days=limit_days)
    rows = db.execute(
        select(SaDataQualityCheck)
        .where(
            SaDataQualityCheck.check_name == CHECK_NAME,
            SaDataQualityCheck.check_date >= since,
        )
        .order_by(
            SaDataQualityCheck.check_date.desc(), SaDataQualityCheck.metric_name
        )
    ).scalars().all()
    if not rows:
        return {"as_of": None, "factors": []}
    latest_date = rows[0].check_date
    latest = [r for r in rows if r.check_date == latest_date]

    by_factor: dict[str, dict] = {}
    for r in latest:
        code = (r.detail or {}).get("factor_code") or r.metric_name.partition(":")[2]
        entry = by_factor.setdefault(
            code, {"factor_code": code, "check_date": latest_date.isoformat(), "metrics": {}}
        )
        entry["metrics"][r.metric_name.split(":")[0]] = {
            "value": float(r.metric_value) if r.metric_value is not None else None,
            "status": r.status,
        }
    factors = list(by_factor.values())
    for f in factors:
        f["worst"] = (
            "fail"
            if any(m["status"] == "fail" for m in f["metrics"].values())
            else "warn"
            if any(m["status"] == "warn" for m in f["metrics"].values())
            else "pass"
        )
    return {"as_of": latest_date.isoformat(), "factors": factors}
