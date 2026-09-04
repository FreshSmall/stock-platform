"""Dirty-data detection & repair for the daily-K stores (V2.1 BP-V2.1-003).

Productizes the one-off scripts behind the step1 report findings:

* **price freeze** — ≥ ``FREEZE_RUN_LEN`` consecutive bars with an unchanged
  close and a flat/NULL pct (degraded-source garbage, e.g. 001331's stuck
  series; 576 cells / 141 stocks found 2026-08-21);
* **segment misalignment** — a sustained run of bars whose price level is
  > ``MISALIGN_RATIO`` away from the stock's own historical median (e.g.
  600066 quoting 0.2–0.6 元 for months against a ~10 元 reality; 30 rows /
  17 stocks);
* repair = re-fetch the affected windows through the normal (multi-source,
  retried) fetch chain and UPSERT into whichever stores are active.

Detection runs against ``daily_prices`` (authoritative until the v2 cutover);
repair writes both stores while the migration window is open.
"""

import logging
import time
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.data import sync_daily, sync_kline
from app.models.stock import DailyPrice

logger = logging.getLogger(__name__)

FREEZE_RUN_LEN = 5          # consecutive flat bars → frozen
MISALIGN_RATIO = 5.0        # close / expanding-median outside [1/r, r] → suspect
MISALIGN_RUN_LEN = 3        # consecutive suspect bars → misaligned segment
REPAIR_PAUSE_SEC = 0.3      # pacing between codes during repair
REPAIR_CIRCUIT_BREAKER = 30  # consecutive failures that abort a repair run


# ---------------------------------------------------------------------------
# Detection (pure functions over a per-stock frame — unit-test friendly)
# ---------------------------------------------------------------------------

def frozen_segments(df: pd.DataFrame) -> list[tuple[date, date, int]]:
    """Flat-close segments in one stock's frame.

    ``df`` needs ascending ``trade_date``, ``close``, ``pct_change`` columns.
    Returns ``(first_date, last_date, bar_count)`` tuples for runs of
    ≥ ``FREEZE_RUN_LEN`` bars where close is unchanged and pct is flat/NULL.
    """
    flat = (df["close"].ffill() == df["close"].shift()).fillna(False) & (
        df["pct_change"].fillna(0).abs() < 1e-9
    )
    segs: list[tuple[date, int]] = []
    run_start: date | None = None
    run_len = 0  # consecutive flat TRANSITIONS; bars in the run = run_len + 1
    for ts, is_flat in flat.items():
        if is_flat:
            run_len += 1
            if run_start is None:
                run_start = ts
        else:
            if run_start is not None and run_len + 1 >= FREEZE_RUN_LEN:
                segs.append((run_start, run_len + 1))
            run_start, run_len = None, 0
    if run_start is not None and run_len + 1 >= FREEZE_RUN_LEN:
        segs.append((run_start, run_len + 1))
    out: list[tuple[date, date, int]] = []
    for start_ts, n in segs:
        # start_ts is the SECOND bar of the equal run; back up one bar so the
        # segment covers all frozen bars.
        loc = df.index.get_loc(start_ts)
        first_ts = df.index[max(loc - 1, 0)]
        end_ts = df.index[min(loc + n - 2, len(df) - 1)]
        out.append((first_ts, end_ts, n))
    return out


def misaligned_segments(df: pd.DataFrame) -> list[tuple[date, date, int]]:
    """Segments whose price level breaks away from the stock's own history.

    Compares each bar's close against the expanding median of the PRIOR
    ``MISALIGN_BUFFER`` bars (so the test itself can't be dragged along by a
    long bad segment); a run of ≥ ``MISALIGN_RUN_LEN`` bars outside
    ``[1/MISALIGN_RATIO, MISALIGN_RATIO]`` is flagged.
    """
    MISALIGN_BUFFER = 60
    med = df["close"].shift(1).rolling(MISALIGN_BUFFER, min_periods=10).median()
    ratio = df["close"] / med
    suspect = (ratio > MISALIGN_RATIO) | (ratio < 1.0 / MISALIGN_RATIO)
    suspect = suspect.fillna(False)

    segs: list[tuple[date, int]] = []
    run_start = None
    run_len = 0
    for ts, bad in suspect.items():
        if bad:
            run_len += 1
            if run_start is None:
                run_start = ts
        else:
            if run_start is not None and run_len >= MISALIGN_RUN_LEN:
                segs.append((run_start, run_len))
            run_start, run_len = None, 0
    if run_start is not None and run_len >= MISALIGN_RUN_LEN:
        segs.append((run_start, run_len))

    out = []
    for start_ts, n in segs:
        loc = df.index.get_loc(start_ts)
        end_ts = df.index[min(loc + n - 1, len(df) - 1)]
        out.append((start_ts, end_ts, n))
    return out


# ---------------------------------------------------------------------------
# DB scans
# ---------------------------------------------------------------------------

def _load_window(db: Session, lookback_days: int) -> pd.DataFrame:
    since = date.today() - timedelta(days=lookback_days)
    rows = db.execute(
        select(
            DailyPrice.stock_code,
            DailyPrice.trade_date,
            DailyPrice.close,
            DailyPrice.pct_change,
        )
        .where(DailyPrice.trade_date >= since)
        .order_by(DailyPrice.stock_code, DailyPrice.trade_date.asc())
    ).all()
    return pd.DataFrame(
        rows, columns=["stock_code", "trade_date", "close", "pct_change"]
    )


def _scan(db: Session, detector, lookback_days: int) -> dict[str, list]:
    df = _load_window(db, lookback_days)
    findings: dict[str, list] = {}
    if df.empty:
        return findings
    for code, g in df.groupby("stock_code"):
        g = g.dropna(subset=["close"]).set_index("trade_date")
        if len(g) < FREEZE_RUN_LEN:
            continue
        segs = detector(g)
        if segs:
            findings[code] = [
                {"start": s, "end": e, "bars": n} for s, e, n in segs
            ]
    return findings


def find_frozen(db: Session, lookback_days: int = 120) -> dict[str, list]:
    """``{code: [{start, end, bars}, ...]}`` for frozen-close segments."""
    return _scan(db, frozen_segments, lookback_days)


def find_misaligned(db: Session, lookback_days: int = 365 * 3) -> dict[str, list]:
    """``{code: [{start, end, bars}, ...]}`` for misaligned price segments."""
    return _scan(db, misaligned_segments, lookback_days)


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

def repair_codes(
    db: Session,
    codes: list[str],
    start: str,
    end: str,
    max_bars: int = 640,
) -> dict:
    """Re-fetch ``[start, end]`` for ``codes`` and upsert into active stores.

    Goes through the standard fetch chain (Tencent primary/secondary →
    eastmoney, retried) rather than the old script's tencent-only bypass —
    the chain's ordering already prefers Tencent and inherits the retry +
    WAF-cooldown machinery. Paces itself; aborts after
    ``REPAIR_CIRCUIT_BREAKER`` consecutive failures.
    """
    write_legacy = settings.kline_source == "legacy"
    write_v2 = settings.kline_source == "v2" or settings.kline_rebuild_enabled
    repaired: dict[str, int] = {}
    failed: list[str] = []
    consecutive = 0
    for i, code in enumerate(codes):
        if i:
            time.sleep(REPAIR_PAUSE_SEC)
        try:
            rows = 0
            if write_legacy:
                rows += sync_daily.sync_one_stock(db, code, start, end, max_bars=max_bars)
            if write_v2:
                rows += sync_kline.sync_one_stock_v2(db, code, start, end, max_bars=max_bars)
            repaired[code] = rows
            consecutive = 0
        except Exception as e:  # noqa: BLE001 - per-code resilience
            failed.append(code)
            consecutive += 1
            logger.error("repair failed for %s: %s", code, e)
            if consecutive >= REPAIR_CIRCUIT_BREAKER:
                logger.error("repair: %d consecutive failures, aborting", consecutive)
                break
    return {"repaired": repaired, "failed": failed}


def repair_findings(db: Session, findings: dict[str, list], pad_days: int = 10) -> dict:
    """Repair every segment found by :func:`find_frozen` / :func:`find_misaligned`.

    Each segment is re-fetched with a small pad on both sides (so the join
    with surrounding good bars is re-stitched too). A code with several
    segments gets one fetch spanning the outermost dates.
    """
    per_code: dict[str, tuple[str, str]] = {}
    for code, segs in findings.items():
        starts = [s["start"] for s in segs]
        ends = [s["end"] for s in segs]
        lo = min(starts) - timedelta(days=pad_days)
        hi = max(ends) + timedelta(days=pad_days)
        per_code[code] = (lo.strftime("%Y%m%d"), hi.strftime("%Y%m%d"))

    all_results = {"repaired": {}, "failed": []}
    for code, (start, end) in per_code.items():
        res = repair_codes(db, [code], start, end)
        all_results["repaired"].update(res["repaired"])
        all_results["failed"].extend(res["failed"])
    return all_results


def run_full_repair(db: Session) -> dict:
    """Admin task entry: detect frozen + misaligned, repair, return summary."""
    frozen = find_frozen(db)
    mis = find_misaligned(db)
    summary = {
        "frozen_codes": len(frozen),
        "misaligned_codes": len(mis),
    }
    if frozen:
        res = repair_findings(db, frozen)
        summary["frozen_repaired"] = len(res["repaired"])
        summary["frozen_failed"] = res["failed"]
    if mis:
        res = repair_findings(db, mis)
        summary["misaligned_repaired"] = len(res["repaired"])
        summary["misaligned_failed"] = res["failed"]
    return summary


# ---------------------------------------------------------------------------
# V2.1 E3: amount/turnover backfill (BP-V2.1-008, P2)
# ---------------------------------------------------------------------------

def find_amount_gaps(db: Session, limit_codes: int = 200) -> dict[str, list]:
    """Segments of ``sa_kline_daily`` rows with NULL amount, per code.

    The primary fetch (Tencent ALT extended) carries amount/turnover, so gaps
    come from segments served by the 6-field classic host. Returns
    ``{code: [{start, end}, ...]}`` (contiguous runs), capped at
    ``limit_codes`` codes per call — the long task iterates.
    """
    from app.models.kline import SaKlineDaily

    rows = db.execute(
        select(
            SaKlineDaily.stock_code,
            SaKlineDaily.trade_date,
        )
        .where(SaKlineDaily.amount.is_(None))
        .order_by(SaKlineDaily.stock_code, SaKlineDaily.trade_date.asc())
        .limit(200_000)
    ).all()

    # Group into contiguous segments per code (trade-date gaps split runs so a
    # later suspension doesn't stitch two far-apart gaps into one fetch).
    segments: dict[str, list] = {}
    prev_code, prev_date = None, None
    for code, d in rows:
        segs = segments.setdefault(code, [])
        if code != prev_code or prev_date is None or (d - prev_date).days > 15:
            segs.append({"start": d, "end": d})
        else:
            segs[-1]["end"] = d
        prev_code, prev_date = code, d

    return dict(list(segments.items())[:limit_codes])


def backfill_amount(db: Session, limit_codes: int = 200) -> dict:
    """Re-fetch amount/turnover for gap segments (admin long task, iterated).

    Goes through the standard fetch chain (ALT extended carries the fields);
    the upsert only matters for the amount/turnover columns on rows that
    already exist. Idempotent — re-running continues where the last pass
    stopped (gaps shrink as they're filled).
    """
    gaps = find_amount_gaps(db, limit_codes=limit_codes)
    if not gaps:
        return {"codes_with_gaps": 0, "repaired": 0, "failed": []}

    repaired = 0
    failed: list[str] = []
    codes = list(gaps.keys())
    for i, code in enumerate(codes):
        if i:
            time.sleep(REPAIR_PAUSE_SEC)
        segs = gaps[code]
        start = min(s["start"] for s in segs)
        end = max(s["end"] for s in segs)
        try:
            rows, source = sync_kline.fetch_raw_with_true_pct(
                code,
                (start - timedelta(days=3)).strftime("%Y%m%d"),
                (end + timedelta(days=3)).strftime("%Y%m%d"),
            )
            # Only re-upsert rows inside the gap segments (avoid touching
            # good rows on the padding days).
            seg_ranges = [(s["start"], s["end"]) for s in segs]
            in_gap = [
                r for r in rows
                if any(
                    lo <= sync_kline._to_date(r["trade_date"]) <= hi
                    for lo, hi in seg_ranges
                )
                and r.get("amount") is not None
            ]
            if in_gap:
                for r in in_gap:
                    r["_source"] = source
                repaired += sync_kline.upsert_kline_rows(db, in_gap, source=source)
        except Exception as e:  # noqa: BLE001 - per-code resilience
            failed.append(code)
            logger.error("amount backfill failed for %s: %s", code, e)
            if len(failed) >= REPAIR_CIRCUIT_BREAKER:
                logger.error("amount backfill: %d failures, aborting", len(failed))
                break
    return {
        "codes_with_gaps": len(gaps),
        "repaired": repaired,
        "failed": failed,
    }
