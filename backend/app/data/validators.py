"""Data validation for ingested rows.

Non-fatal by design: logs warnings but never raises. A row is dropped only
when an essential field (stock_code / trade_date / close) is missing or
unparseable. Abnormal ``pct_change`` is logged but the row is still kept,
since ST stocks and resume-trading days can legitimately move > 20%.
"""

import logging
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

# |pct_change| > 20% is suspicious for non-ST stocks; flag but don't drop.
ABNORMAL_PCT_THRESHOLD = Decimal("20")


def validate_daily_row(row: dict) -> bool:
    """Return True if the row is structurally valid.

    Essential fields: ``stock_code``, ``trade_date``, ``close``. A missing one
    logs a warning and returns False (caller drops the row).

    ``pct_change`` outside ±20% is logged as a warning but does NOT invalidate
    the row (still returns True) — it's informational only.

    :param row: dict produced by :func:`akshare_client.fetch_daily_quotes`.
    :return: True if essential fields are present.
    """
    required = ("stock_code", "trade_date", "close")
    for k in required:
        if row.get(k) is None:
            logger.warning(
                "validation: missing %s in row %s", k, row
            )
            return False
    pct = row.get("pct_change")
    if pct is not None:
        try:
            v = Decimal(str(pct))
            if abs(v) > ABNORMAL_PCT_THRESHOLD:
                logger.warning(
                    "validation: abnormal pct_change=%s for %s @ %s",
                    v,
                    row["stock_code"],
                    row["trade_date"],
                )
        except (InvalidOperation, ValueError):
            logger.warning(
                "validation: unparseable pct_change=%s for %s",
                pct,
                row["stock_code"],
            )
    return True


def find_duplicate_keys(rows: list[dict]) -> list[tuple]:
    """Return ``(stock_code, trade_date)`` pairs that appear more than once.

    Each duplicate key is reported exactly once (on its second sighting), so a
    key seen three times still yields a single entry.

    :param rows: list of dicts with ``stock_code`` and ``trade_date``.
    :return: list of ``(stock_code, trade_date)`` tuples that are duplicated.
    """
    seen: dict[tuple, int] = {}
    dups: list[tuple] = []
    for r in rows:
        key = (r.get("stock_code"), r.get("trade_date"))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            dups.append(key)
    return dups
