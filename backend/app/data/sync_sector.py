"""Sector (板块) sync into ``sa_sector`` + ``sa_sector_stock``.

Fetches the sector definition list (industry/concept) and UPSERTs into
``sa_sector`` on ``uk_code_type``. Sector membership (``sa_sector_stock``) and
daily stats (``sa_sector_daily``) are populated by separate fetchers; this
module focuses on the definition list, which is the prerequisite for the
others. Re-running is safe.
"""

import logging

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data import akshare_client
from app.models.sector import SaSector

logger = logging.getLogger(__name__)


def upsert_sectors(db: Session, rows: list[dict]) -> int:
    """UPSERT sector definition rows.

    :return: number of rows written.
    """
    valid = [r for r in rows if r.get("sector_code") and r.get("sector_type")]
    if not valid:
        return 0
    stmt = mysql_insert(SaSector).values(valid)
    stmt = stmt.on_duplicate_key_update(
        {"sector_name": stmt.inserted.sector_name}
    )
    db.execute(stmt)
    db.commit()
    return len(valid)


def sync_sector_list(db: Session, sector_type: str = "industry") -> int:
    """Fetch + UPSERT the sector definition list for one type.

    :param sector_type: ``'industry'`` or ``'concept'``.
    :return: number of rows written.
    """
    rows = akshare_client.fetch_sector_list(sector_type=sector_type)
    return upsert_sectors(db, rows)


def sync_all(db: Session) -> int:
    """Sync both industry and concept sector lists.

    :return: total rows written.
    """
    total = 0
    for st in ("industry", "concept"):
        try:
            total += sync_sector_list(db, sector_type=st)
        except Exception as e:  # noqa: BLE001 - one type failing shouldn't abort the other
            logger.error("sector sync failed for %s: %s", st, e)
    return total
