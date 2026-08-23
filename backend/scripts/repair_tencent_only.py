"""Repair pass for damaged/partial daily-K days — TENCENT-ONLY fetch.

Damage inventory (2026-08-17):
- 2026-06-30: 1361 of 1467 rows lack pct_change (old truncation + garbage).
- 2026-07-28: 778 rows were overwritten by degraded eastmoney frames
  (pct NULL, amount 0, wrong volume unit) during the second heal pass.
- 2026-07-29: original settled rows vanished mid-repair; only ~1215 remain.

The eastmoney soft-ban serves well-formed-but-blank frames, so this pass
bypasses fetch_daily_quotes and calls the two Tencent fetchers directly
(primary host, then the alternate host), then UPSERTs via the normal
sync_daily path (idempotent). A code counts as needing repair when it has
fewer than the expected settled rows across the window.
"""

import logging
import time

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.data.akshare_client import (
    _TENCENT_KLINE,
    _TENCENT_KLINE_ALT,
    _fetch_daily_quotes_tencent,
)
from app.data.sync_daily import upsert_daily_rows
from app.models.stock import DailyPrice, StockPool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

WINDOWS = [
    ("20260629", "20260630"),
    ("20260728", "20260729"),
]
PAUSE_SEC = 0.3


def fetch_tencent(code: str, start: str, end: str) -> list[dict]:
    rows = _fetch_daily_quotes_tencent(code, start, end, url=_TENCENT_KLINE)
    if rows:
        return rows
    return _fetch_daily_quotes_tencent(
        code, start, end, url=_TENCENT_KLINE_ALT, extended=True
    )


def main() -> None:
    db = SessionLocal()
    try:
        latest_sp = db.execute(
            select(func.max(StockPool.trade_date)).select_from(StockPool)
        ).scalar()
        codes = (
            db.execute(
                select(StockPool.stock_code).where(StockPool.trade_date == latest_sp)
            )
            .scalars()
            .all()
        )
        print(f"pool snapshot {latest_sp}: {len(codes)} codes", flush=True)

        for start, end in WINDOWS:
            d0, d1 = f"{start[:4]}-{start[4:6]}-{start[6:]}", f"{end[:4]}-{end[4:6]}-{end[6:]}"
            n_settled = dict(
                db.execute(
                    select(DailyPrice.stock_code, func.count()).where(
                        DailyPrice.trade_date.in_((d0, d1)),
                        DailyPrice.pct_change.is_not(None),
                    )
                    .group_by(DailyPrice.stock_code)
                ).all()
            )
            # rows with NULL pct on either day are garbage → also resync
            garbage = set(
                db.execute(
                    select(DailyPrice.stock_code).where(
                        DailyPrice.trade_date.in_((d0, d1)),
                        DailyPrice.pct_change.is_(None),
                    )
                )
                .scalars()
                .all()
            )
            todo = [c for c in codes if n_settled.get(c, 0) < 2 or c in garbage]
            print(
                f"window {d0}..{d1}: {len(todo)} codes need repair "
                f"({len(garbage)} with garbage rows)",
                flush=True,
            )
            written = fails = no_bar = 0
            consecutive = 0
            for i, code in enumerate(todo, 1):
                try:
                    rows = fetch_tencent(code, start, end)
                    if rows:
                        written += upsert_daily_rows(db, rows)
                    else:
                        no_bar += 1
                    consecutive = 0
                except Exception as e:  # noqa: BLE001
                    fails += 1
                    consecutive += 1
                    if fails <= 10:
                        print(f"FAIL {code}: {e}", flush=True)
                    if consecutive >= 30:
                        print("circuit breaker tripped, aborting window", flush=True)
                        break
                if i % 300 == 0:
                    print(
                        f"progress {i}/{len(todo)} written={written} "
                        f"fails={fails} no_bar={no_bar}",
                        flush=True,
                    )
                time.sleep(PAUSE_SEC)
            for d in (d0, d1):
                n, s = db.execute(
                    select(func.count(), func.sum(DailyPrice.pct_change.is_not(None)))
                    .where(DailyPrice.trade_date == d)
                ).one()
                print(f"  {d}: total={n} settled={s}", flush=True)
            print(f"window done: written={written} fails={fails} no_bar={no_bar}", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
