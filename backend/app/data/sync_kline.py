"""Write path for the V2.1 raw K-line store: ``sa_kline_daily`` + ``sa_adjust_factor``.

Storage model (spec-004 BP-V2.1-001):

* ``sa_kline_daily`` holds UN-adjusted (raw) OHLCV. ``pct_change`` is overlaid
  from the qfq series (or eastmoney's published 涨跌幅) and is the *true*
  post-adjustment return — the anchor of the whole system.
* ``sa_adjust_factor`` holds a **piecewise-constant** cumulative factor per
  day: ``hfq_close = raw_close * factor``; jumps only on ex-dividend/split
  days; ``qfq_close = raw_close * factor / factor_latest``.

Factor derivation (revised after live verification 2026-08-31):

The original plan anchored factors from ``hfq_close / raw_close`` per day.
Live testing against Tencent showed their hfq series is NOT a pure multiple
of raw between corporate actions — it jitters ±0.3% day-to-day (only the
ex-div jump, e.g. 600519 on 2021-06-25 +0.85%, is a real signal). Daily
ratios would bake that noise into every factor row.

So factors are derived purely from the raw series + the true pct anchor:

    r_raw = raw_t / raw_{t-1}
    r_true = 1 + pct_t / 100
    if |r_true - r_raw| > TOL:        # corporate action detected
        factor_t = factor_{t-1} * r_true / r_raw
    else:
        factor_t = factor_{t-1}       # unchanged — piecewise constant

The jump test uses the DIFFERENCE metric (not the ratio) so the audit helper
:func:`hfq_close_2_close_deviation` — same formula, same tolerance — passes
by construction on days the gate left alone (observed live: a ratio test at
0.49% squeaked under the gate while the difference audit read 0.5047%).
Between events the factor is exact (no accumulated drift); on the event day
the jump inherits only the raw 2dp rounding error.
"""

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data import akshare_client, validators
from app.data.sync_daily import _to_date
from app.models.kline import SaAdjustFactor, SaKlineDaily

logger = logging.getLogger(__name__)

# Deviation gate for a factor jump (corporate-action detector). Matches the
# 0.5% tolerance used by scripts/check_adjustment.py and the quality patrol.
TOL = 0.005


# ---------------------------------------------------------------------------
# Row writes
# ---------------------------------------------------------------------------

def upsert_kline_rows(db: Session, rows: list[dict], source: str | None = None) -> int:
    """UPSERT validated RAW rows into ``sa_kline_daily``.

    Same contract as :func:`app.data.sync_daily.upsert_daily_rows` (idempotent
    on the ``uk_code_date`` unique key), plus the ``source`` provenance column.
    Commits here; returns the number of rows written post-validation.
    """
    valid = [r for r in rows if validators.validate_daily_row(r)]
    if not valid:
        return 0
    payload = [
        {
            "stock_code": r["stock_code"],
            "trade_date": _to_date(r["trade_date"]),
            "open": r.get("open"),
            "close": r.get("close"),
            "high": r.get("high"),
            "low": r.get("low"),
            "volume": r.get("volume"),
            "amount": r.get("amount"),
            "pct_change": r.get("pct_change"),
            "turnover": r.get("turnover"),
            "source": r.get("_source") or source,
        }
        for r in valid
    ]
    stmt = mysql_insert(SaKlineDaily).values(payload)
    update_cols = {
        c: getattr(stmt.inserted, c)
        for c in (
            "open", "close", "high", "low", "volume", "amount",
            "pct_change", "turnover", "source",
        )
    }
    stmt = stmt.on_duplicate_key_update(update_cols)
    db.execute(stmt)
    db.commit()
    return len(payload)


def fetch_raw_with_true_pct(
    symbol: str, start_date: str, end_date: str, max_bars: int = 640
) -> tuple[list[dict], str | None]:
    """Fetch raw bars with the TRUE pct overlaid from the qfq series.

    The raw fetch's own client-side pct is computed from raw closes — wrong
    on ex-dividend days (see :func:`akshare_client.fetch_daily_quotes_raw`).
    The qfq series carries the true daily return, so we join by date and
    overlay. Rows missing from the qfq response keep the raw-side pct
    (usually only a trailing bar difference between the two fetches).

    :return: ``(rows, raw_source)`` where ``raw_source`` is ``"tencent"`` or
        ``"em"`` (``None`` when the raw fetch came back empty).
    """
    raw_rows = akshare_client.fetch_daily_quotes_raw(symbol, start_date, end_date, max_bars=max_bars)
    if not raw_rows:
        return [], None
    raw_source = raw_rows[0].get("_source") or "tencent"

    qfq_rows = akshare_client.fetch_daily_quotes(symbol, start_date, end_date, max_bars=max_bars)
    pct_by_date = {r["trade_date"]: r.get("pct_change") for r in qfq_rows}

    out: list[dict] = []
    for r in raw_rows:
        true_pct = pct_by_date.get(r["trade_date"])
        if true_pct is not None:
            r["pct_change"] = true_pct
        out.append(r)
    return out, raw_source


# ---------------------------------------------------------------------------
# Factor chain (pure functions — unit-tested without DB/network)
# ---------------------------------------------------------------------------

def compute_factor_chain(rows: list[dict]) -> list[dict]:
    """Derive the piecewise-constant factor chain from ascending raw rows.

    ``rows`` must be sorted ascending by ``trade_date`` and carry ``close``
    and ``pct_change`` (the true return, %). Returns factor rows shaped
    ``{"trade_date": date, "adj_factor": float}`` — one per input row; the
    first row gets factor 1.0. Rows with missing close/pct carry the previous
    factor forward.
    """
    out: list[dict] = []
    factor = 1.0
    prev_close: float | None = None
    for r in rows:
        d = _to_date(r.get("trade_date"))
        close = r.get("close")
        pct = r.get("pct_change")
        if close is not None and prev_close and pct is not None:
            r_raw = float(close) / float(prev_close)
            r_true = 1.0 + float(pct) / 100.0
            if r_raw > 0 and abs(r_true - r_raw) > TOL:
                factor = factor * r_true / r_raw
        if close is not None:
            prev_close = float(close)
        out.append({"trade_date": d, "adj_factor": factor})
    return out


def detect_factor_events(rows: list[dict]) -> list[date]:
    """Dates where the true return deviates from the raw return (corporate actions)."""
    events: list[date] = []
    prev_close: float | None = None
    for r in rows:
        d = _to_date(r.get("trade_date"))
        close = r.get("close")
        pct = r.get("pct_change")
        if close is not None and prev_close and pct is not None:
            r_raw = float(close) / float(prev_close)
            r_true = 1.0 + float(pct) / 100.0
            if r_raw > 0 and abs(r_true - r_raw) > TOL:
                events.append(d)
        if close is not None:
            prev_close = float(close)
    return events


def hfq_close_2_close_deviation(rows: list[dict], factors: list[dict]) -> list[dict]:
    """Audit helper: rows whose hfq close-to-close return deviates from pct.

    ``rows`` and ``factors`` must be parallel (same dates, ascending). Builds
    ``hfq = raw * factor`` and flags positions where
    ``|hfq_t/hfq_{t-1} - (1+pct_t/100)| > TOL``. This is the productized form
    of ``scripts/check_adjustment.py`` for the new store — after a clean
    re-ingest it must return ``[]`` for every stock.
    """
    bad: list[dict] = []
    prev_hfq: float | None = None
    for r, f in zip(rows, factors):
        close = r.get("close")
        pct = r.get("pct_change")
        if close is None or f.get("adj_factor") is None:
            continue
        hfq = float(close) * float(f["adj_factor"])
        if prev_hfq and pct is not None:
            dev = abs(hfq / prev_hfq - (1.0 + float(pct) / 100.0))
            if dev > TOL:
                bad.append(
                    {
                        "trade_date": _to_date(r.get("trade_date")),
                        "deviation": round(dev, 6),
                        "close": float(close),
                        "factor": float(f["adj_factor"]),
                        "pct_change": pct,
                    }
                )
        prev_hfq = hfq
    return bad


# ---------------------------------------------------------------------------
# DB round-trips for factors
# ---------------------------------------------------------------------------

def upsert_factor_rows(db: Session, code: str, factors: list[dict], anchored: bool = True) -> int:
    """UPSERT factor rows for ``code``. Idempotent on the unique key."""
    if not factors:
        return 0
    payload = [
        {
            "stock_code": code,
            "trade_date": f["trade_date"],
            "adj_factor": round(float(f["adj_factor"]), 8),
            "anchored": 1 if anchored else 0,
        }
        for f in factors
        if f.get("trade_date") is not None
    ]
    if not payload:
        return 0
    stmt = mysql_insert(SaAdjustFactor).values(payload)
    update_cols = {
        c: getattr(stmt.inserted, c) for c in ("adj_factor", "anchored")
    }
    stmt = stmt.on_duplicate_key_update(update_cols)
    db.execute(stmt)
    db.commit()
    return len(payload)


def _load_kline_rows(db: Session, code: str) -> list[SaKlineDaily]:
    return list(
        db.execute(
            select(SaKlineDaily)
            .where(SaKlineDaily.stock_code == code)
            .order_by(SaKlineDaily.trade_date.asc())
        )
        .scalars()
        .all()
    )


def init_adjust_factors(db: Session, code: str) -> int:
    """(Re)derive the full factor chain for ``code`` from its stored raw bars.

    Full-history pass — used by the re-ingest tick and by the corporate-action
    re-anchor. Overwrites any previously stored factors for the range covered
    by the bars.
    """
    bars = _load_kline_rows(db, code)
    if not bars:
        return 0
    rows = [{"trade_date": b.trade_date, "close": b.close, "pct_change": b.pct_change} for b in bars]
    factors = compute_factor_chain(rows)
    return upsert_factor_rows(db, code, factors, anchored=True)


def maintain_factor_incremental(db: Session, code: str, new_rows: list[dict]) -> bool:
    """Extend the factor chain for freshly upserted bars; detect events.

    Appends factors for ``new_rows`` continuing from the last stored factor
    (or a fresh chain when none exist). Returns True when a corporate action
    (factor jump) was detected among the new rows — the caller re-anchors the
    whole chain via :func:`init_adjust_factors` and schedules a full re-ingest
    for the stock.
    """
    if not new_rows:
        return False
    ordered = sorted(new_rows, key=lambda r: str(r.get("trade_date")))
    last = db.execute(
        select(SaAdjustFactor)
        .where(SaAdjustFactor.stock_code == code)
        .order_by(SaAdjustFactor.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last is None:
        init_adjust_factors(db, code)
        return bool(detect_factor_events(ordered))

    # Seed the pass with the last stored bar's CLOSE (from sa_kline_daily, at
    # or before the factor date) so the first new bar's raw return is measured
    # against the stock's real previous close — otherwise a dividend on the
    # very first new day would go undetected.
    first_new_date = min(_to_date(r.get("trade_date")) for r in ordered)
    prev_bar = db.execute(
        select(SaKlineDaily)
        .where(
            SaKlineDaily.stock_code == code,
            SaKlineDaily.trade_date < first_new_date,
        )
        .order_by(SaKlineDaily.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    seed = {
        "trade_date": prev_bar.trade_date if prev_bar else last.trade_date,
        "close": float(prev_bar.close) if prev_bar and prev_bar.close is not None else None,
        "pct_change": None,  # the seed never triggers a jump itself
    }
    chain = compute_factor_chain([seed] + ordered)
    new_factors = chain[1:]
    # compute_factor_chain starts at 1.0 — rebase onto the stored factor.
    base = float(last.adj_factor)
    for f in new_factors:
        f["adj_factor"] = f["adj_factor"] * base
    upsert_factor_rows(db, code, new_factors, anchored=False)
    # Detect on the SEEDED sequence: the first new bar's corporate action is
    # only visible relative to the previous stored close.
    return bool(detect_factor_events([seed] + ordered))


def sync_one_stock_v2(
    db: Session, code: str, start_date: str, end_date: str, max_bars: int = 640
) -> int:
    """Fetch raw+true-pct, upsert ``sa_kline_daily``, maintain factors.

    One stock, one window — the unit both the daily incremental dual-write
    (17:30) and the re-ingest tick (chunked full history) call. Returns the
    number of bars written. A detected corporate action re-anchors the chain
    immediately (full-history re-derivation is cheap: pure math over stored
    bars) — the *data* re-fetch for the event stock is left to the rebuild
    queue, since re-anchoring makes existing bars consistent already.
    """
    rows, source = fetch_raw_with_true_pct(code, start_date, end_date, max_bars=max_bars)
    if not rows:
        return 0
    for r in rows:
        r["_source"] = source
    written = upsert_kline_rows(db, rows, source=source)
    event = maintain_factor_incremental(db, code, rows)
    if event:
        init_adjust_factors(db, code)
        logger.info("adjust event detected for %s in %s..%s — chain re-anchored", code, start_date, end_date)
    return written
