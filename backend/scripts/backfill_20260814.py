"""One-off repair: back-fill daily-K for codes missing 2026-08-14.

Background: the 2026-08-14 17:30 full sync never completed (backend restarted
at 22:31, past the window). A partial earlier run left 1546/4216 codes present
with settled bars, so startup gap detection (which only looks at MAX
trade_date) saw "up to date" and the 23:00 retry had no failure list to
replay.

Both stock sources used by the app (web.ifzq.gtimg.cn / push2his.eastmoney.com)
are currently IP-blocked (WAF 501 / connection reset) after a request burst, so
this script pulls from Tencent's alternate kline host
``proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get`` — same qfq
series and bar schema as ``_fetch_daily_quotes_tencent``, plus amount/turnover
in the trailing fields. Rows go through the app's own validation + UPSERT path
(``sync_daily.upsert_daily_rows``), keyed on (stock_code, trade_date), so the
run is idempotent.

The requests session has ``trust_env=False`` so the machine's local system
proxy (127.0.0.1:7890) is bypassed — it is currently refusing connections to
the finance hosts.
"""

import logging
import time

import requests
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.data.akshare_client import _tencent_prefix
from app.data.sync_daily import upsert_daily_rows
from app.models.stock import DailyPrice, StockPool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

TARGET_DATE = "2026-08-14"
TARGET = "20260814"
URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
BARS = 59  # enough history for a prev-close before the target date
PAUSE_SEC = 0.35
BATCH = 50  # codes per upsert call


def fetch_one(sess: requests.Session, code: str) -> list[dict]:
    sym = f"{_tencent_prefix(code)}{code}"
    r = sess.get(
        URL,
        params={"param": f"{sym},day,,,{BARS},qfq"},
        timeout=12,
    )
    r.raise_for_status()
    data = (r.json().get("data") or {}).get(sym) or {}
    bars = data.get("qfqday") or data.get("day") or []

    rows: list[dict] = []
    prev_close = None
    for it in bars:
        if not isinstance(it, list) or len(it) < 6:
            continue
        d_str = it[0]
        close = float(it[2]) if it[2] else None
        if d_str == TARGET_DATE and close is not None:
            pct = None
            if prev_close:
                pct = round((close - prev_close) / prev_close * 100, 4)
            amount = None
            if len(it) > 8 and it[8]:
                amount = round(float(it[8]) * 10000, 2)  # 万元 → 元
            turnover = None
            if len(it) > 7 and it[7]:
                turnover = float(it[7])  # 换手率 %
            rows.append(
                {
                    "stock_code": code,
                    "trade_date": d_str,
                    "open": float(it[1]) if it[1] else None,
                    "close": close,
                    "high": float(it[3]) if it[3] else None,
                    "low": float(it[4]) if it[4] else None,
                    "volume": int(float(it[5])) if it[5] else None,
                    "amount": amount,
                    "pct_change": pct,
                    "turnover": turnover,
                }
            )
        elif d_str < TARGET_DATE and close is not None:
            prev_close = close
    return rows


def main() -> None:
    db = SessionLocal()
    try:
        latest_sp = db.execute(
            select(func.max(StockPool.trade_date)).select_from(StockPool)
        ).scalar()
        codes = (
            db.execute(
                select(StockPool.stock_code).where(
                    StockPool.trade_date == latest_sp
                )
            )
            .scalars()
            .all()
        )
        have = set(
            db.execute(
                select(DailyPrice.stock_code).where(
                    DailyPrice.trade_date == TARGET_DATE
                )
            )
            .scalars()
            .all()
        )
        missing = [c for c in codes if c not in have]
        print(
            f"pool snapshot {latest_sp}: {len(codes)} codes, "
            f"{len(have)} already have {TARGET_DATE}, syncing {len(missing)}",
            flush=True,
        )

        sess = requests.Session()
        sess.trust_env = False  # bypass local system proxy
        sess.headers.update({"User-Agent": "Mozilla/5.0"})

        written = 0
        fails: list[str] = []
        no_bar: list[str] = []
        pending: list[dict] = []
        consecutive = 0
        for i, code in enumerate(missing, 1):
            try:
                rows = fetch_one(sess, code)
                if rows:
                    pending.extend(rows)
                else:
                    no_bar.append(code)
                consecutive = 0
            except Exception as e:  # noqa: BLE001 - per-code resilience
                fails.append(code)
                consecutive += 1
                if len(fails) <= 10:
                    print(f"FAIL {code}: {e}", flush=True)
                if consecutive >= 30:
                    print("circuit breaker: 30 consecutive failures, aborting", flush=True)
                    break
            if len(pending) >= BATCH:
                written += upsert_daily_rows(db, pending)
                pending = []
            if i % 200 == 0:
                print(
                    f"progress {i}/{len(missing)} written={written} "
                    f"fails={len(fails)} no_bar={len(no_bar)}",
                    flush=True,
                )
            time.sleep(PAUSE_SEC)
        if pending:
            written += upsert_daily_rows(db, pending)

        print(
            f"DONE written={written} fails={len(fails)} no_bar={len(no_bar)}",
            flush=True,
        )
        if fails:
            print("failed: " + ",".join(fails), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
