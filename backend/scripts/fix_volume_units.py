"""One-off repair: normalize ``daily_prices.volume`` to 手 (lots).

BACKGROUND
----------
``daily_prices.volume`` historically stored a mix of two units:

* Eastmoney rows (``ak.stock_zh_a_hist`` fallback) stored volume in **股**
  (shares) — the raw eastmoney ``成交量`` value. These rows are identifiable
  by ``amount IS NOT NULL`` (eastmoney returns 成交额; Tencent does not).
* Tencent rows (the primary source since 2026-07-29) stored volume in **手**
  (1 手 = 100 股) — the raw Tencent kline value.

So the same stock's volume could jump 100× between consecutive days depending
on which source happened to win. The code fix (``akshare_client._shares_to_lots``)
normalizes new eastmoney writes to 手 going forward; this script fixes the
~1.1M legacy eastmoney rows already in the DB by dividing their ``volume``
by 100.

HEURISTIC FOR AFFECTED ROWS
---------------------------
We classify each populated row by the identity ``amount = volume × avg_price``,
i.e. ``r = amount / (volume × close)``:

* ``r ≈ 1``    → volume was in **股** (shares)   → rewrite to 手 (÷100).
* ``r ≈ 100``  → volume was already in **手**     → leave untouched.

A histogram of ``log10(r)`` over the table shows two clean clusters with a
wide empty gap between them: ~1.11M rows at ``r ∈ [0.1, 10)`` (股) and ~1.1k
rows at ``r ∈ [100, 1000)`` (手), plus a handful of boundary cases at
``r ∈ [10, 100)``. We therefore use the conservative predicate
``r < 5`` to select rows for the ÷100 rewrite — it captures the entire 股
cluster with margin for limit-up/limit-down and ex-dividend price drift,
while never touching the 手 cluster (all ``r ≥ 100``) or the ambiguous
boundary cases.

NOTE: ``amount IS NOT NULL`` alone is NOT a safe selector — ~1.3k eastmoney-
sourced rows were already ingested in 手 (the external pipeline that
pre-populated ``daily_prices`` for 2021–2026-07 used 手; only our own
akshare fallback wrote 股). The ratio predicate is what makes this safe.

Verified live 2026-08-13 against 600519 / 601398 / 601288.

SAFETY
------
* Default is ``--dry-run``: prints what it WOULD change and exits.
* Must pass ``--apply`` to write.
* Before the UPDATE, materializes a backup of ``(id, stock_code,
  trade_date, volume)`` into ``daily_prices_volume_backup_YYYYMMDD`` so the
  change is reversible.
* Runs inside a single transaction; commits once after the UPDATE.
* Skips rows where ``volume`` is NULL or 0 (÷100 of 0 is 0; nothing changes,
  but we avoid touching them for a cleaner row-affected count).

USAGE
-----
    # 1) Dry run — inspect the before/after sample and the affected count.
    .venv/bin/python scripts/fix_volume_units.py

    # 2) Apply for real (creates backup table, then UPDATEs).
    .venv/bin/python scripts/fix_volume_units.py --apply

    # 3) Verify (separate step, see verify function / just re-query the DB).
    .venv/bin/python scripts/fix_volume_units.py --verify
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

from sqlalchemy import text

from app.core.database import SessionLocal

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("fix_volume_units")

BACKUP_TABLE = f"daily_prices_volume_backup_{date.today().strftime('%Y%m%d')}"

# Predicate selecting rows whose volume is in 股 and needs ÷100.
# ``amount/(volume*close)`` ≈ 1 for 股, ≈ 100 for 手. We pick the 股 cluster
# with a conservative upper bound of 5 (well below the empty gap at [10,100)).
# Requires amount>0, volume>0, close>0 so the ratio is defined and meaningful.
SHARES_PRED = (
    "amount IS NOT NULL AND amount > 0 "
    "AND volume IS NOT NULL AND volume > 0 "
    "AND close IS NOT NULL AND close > 0 "
    "AND amount / (volume * close) < 5"
)


def _affected_count(db) -> int:
    """Number of rows whose volume is in 股 and needs ``÷ 100``."""
    return db.execute(
        text(f"SELECT COUNT(*) FROM daily_prices WHERE {SHARES_PRED}")
    ).scalar()


def dry_run(db) -> None:
    """Print a before/after sample + the total affected count. No writes."""
    n = _affected_count(db)
    log.info("dry-run: %d rows would be updated (volume ÷ 100)", n)

    rows = db.execute(
        text(
            "SELECT stock_code, trade_date, close, volume, amount, "
            "       ROUND(amount/(volume*close), 4) AS r_old, "
            "       ROUND(amount/((volume/100)*close), 4) AS r_new "
            f"FROM daily_prices WHERE {SHARES_PRED} "
            "ORDER BY trade_date DESC LIMIT 8"
        )
    ).fetchall()
    log.info("sample (newest affected rows):")
    log.info(
        "  %-8s %-12s %-9s %-16s %-18s %s",
        "code", "date", "close", "vol(old→new)", "amount", "r=amt/(vol*close)",
    )
    for sc, d, cl, v, a, r_old, r_new in rows:
        log.info(
            "  %-8s %-12s %-9s %d → %d  %-18s %s → %s",
            sc, str(d), str(cl), v, int(v / 100), str(a), r_old, r_new,
        )
    log.info(
        "note: r_old ≈ 1 confirms these were in 股; after ÷100 r_new ≈ 100 "
        "(same scale as the already-手 rows), so all volume becomes 手."
    )


def _create_backup(db) -> None:
    """Snapshot (id, stock_code, trade_date, volume) into a backup table.

    Drops an existing backup of the same name first so the script is
    re-runnable. The backup is what you'd use to roll back.
    """
    db.execute(text(f"DROP TABLE IF EXISTS `{BACKUP_TABLE}`"))
    db.execute(
        text(
            f"CREATE TABLE `{BACKUP_TABLE}` AS "
            "SELECT id, stock_code, trade_date, volume FROM daily_prices"
        )
    )
    n = db.execute(text(f"SELECT COUNT(*) FROM `{BACKUP_TABLE}`")).scalar()
    log.info("backup: %d rows snapshotted into %s", n, BACKUP_TABLE)


def apply(db) -> None:
    """Create backup, then UPDATE volume ÷100 for the 股-sourced rows."""
    n_before = _affected_count(db)
    log.info("apply: %d rows to update", n_before)

    _create_backup(db)

    # Integer divide (FLOOR) keeps volume an integer lot count. volume is a
    # positive multiple of 100 in practice (A-share lots trade in 100s), so
    # FLOOR and ROUND agree; FLOOR is safer against any oddball <100 remainder.
    result = db.execute(
        text(
            "UPDATE daily_prices "
            "SET volume = FLOOR(volume / 100) "
            f"WHERE {SHARES_PRED}"
        )
    )
    written = result.rowcount
    db.commit()
    log.info("apply: UPDATEd %d rows (rowcount=%d)", n_before, written)


def verify(db) -> None:
    """Sanity-check the post-fix state against the amt/vol identity.

    After the fix, EVERY populated row (eastmoney or Tencent) should have
    ``amount / volume ≈ 100 × close`` (volume now in 手). We flag any row
    where the ratio is way off as a potential missed/over-corrected row.
    """
    log.info("verify: checking amount/volume ratio across all populated rows")
    # Expect: amount / volume ≈ close * 100  (since volume is now 手)
    # Allow generous ±50% tolerance — prices drift intraday, and some rows
    # have stale amount. We just want to catch orders-of-magnitude mistakes.
    bad = db.execute(
        text(
            "SELECT COUNT(*) FROM daily_prices "
            "WHERE amount IS NOT NULL AND amount > 0 "
            "  AND volume IS NOT NULL AND volume > 0 "
            "  AND close IS NOT NULL AND close > 0 "
            "  AND NOT (amount / volume BETWEEN close * 100 * 0.5 "
            "                                AND close * 100 * 1.5)"
        )
    ).scalar()
    total = db.execute(
        text(
            "SELECT COUNT(*) FROM daily_prices "
            "WHERE amount IS NOT NULL AND amount > 0 "
            "  AND volume IS NOT NULL AND volume > 0 "
            "  AND close IS NOT NULL AND close > 0"
        )
    ).scalar()
    log.info(
        "verify: %d / %d rows satisfy amount/volume ≈ 100×close "
        "(%d outliers)",
        total - bad, total, bad,
    )
    if bad:
        log.warning(
            "verify: %d rows have an off-ratio — sample:", bad
        )
        rows = db.execute(
            text(
                "SELECT stock_code, trade_date, close, volume, amount, "
                "       ROUND(amount/volume, 4) AS ratio, "
                "       ROUND(close*100, 4) AS expected "
                "FROM daily_prices "
                "WHERE amount IS NOT NULL AND amount > 0 "
                "  AND volume IS NOT NULL AND volume > 0 "
                "  AND close IS NOT NULL AND close > 0 "
                "  AND NOT (amount / volume BETWEEN close * 100 * 0.5 "
                "                                  AND close * 100 * 1.5) "
                "LIMIT 10"
            )
        ).fetchall()
        for sc, d, cl, v, a, ratio, expected in rows:
            log.warning(
                "  %s %s close=%s vol=%s amt=%s ratio=%s expected≈%s",
                sc, str(d), str(cl), v, str(a), ratio, expected,
            )

    # Also confirm the unit split that USED to be visible via amount is gone:
    # both populations should now look the same scale.
    log.info("verify: volume stats by source (post-fix, should be similar scale)")
    rows = db.execute(
        text(
            "SELECT CASE WHEN amount IS NULL THEN 'tencent' ELSE 'eastmoney(post-fix)' END AS src, "
            "       COUNT(*) AS n, MIN(volume) AS vmin, MAX(volume) AS vmax, "
            "       ROUND(AVG(volume)) AS vavg "
            "FROM daily_prices WHERE volume IS NOT NULL AND volume > 0 "
            "GROUP BY src"
        )
    ).fetchall()
    for src, n, vmin, vmax, vavg in rows:
        log.info("  %s: n=%d min=%s max=%s avg=%s", src, n, vmin, vmax, vavg)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply", action="store_true",
        help="Actually write (default is a no-op dry run).",
    )
    mode.add_argument(
        "--verify", action="store_true",
        help="Only run the post-fix sanity check (use after --apply).",
    )
    args = p.parse_args()

    db = SessionLocal()
    try:
        if args.verify:
            verify(db)
        elif args.apply:
            apply(db)
        else:
            dry_run(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
