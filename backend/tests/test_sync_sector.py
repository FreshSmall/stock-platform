"""Tests for app.data.sync_sector against the LIVE DB.

SAFETY: akshare mocked; writes under sentinel sector codes; scrubbed in finally.
"""

import pytest
from sqlalchemy import delete, select

from app.data import sync_sector
from app.models.sector import SaSector

S_CODE = "ZZSEC"
S_TYPE = "industry"


@pytest.fixture
def cleanup(db_session):
    db_session.execute(
        delete(SaSector).where(SaSector.sector_code == S_CODE)
    )
    db_session.commit()
    try:
        yield db_session
    finally:
        db_session.execute(
            delete(SaSector).where(SaSector.sector_code == S_CODE)
        )
        db_session.commit()


def test_upsert_sectors(cleanup):
    db = cleanup
    rows = [{"sector_code": S_CODE, "sector_name": "测试板块", "sector_type": S_TYPE}]
    assert sync_sector.upsert_sectors(db, rows) == 1
    r = db.execute(select(SaSector).where(SaSector.sector_code == S_CODE)).scalar_one()
    assert r.sector_name == "测试板块"


def test_upsert_idempotent_and_updates_name(cleanup):
    db = cleanup
    sync_sector.upsert_sectors(db, [{"sector_code": S_CODE, "sector_name": "A", "sector_type": S_TYPE}])
    sync_sector.upsert_sectors(db, [{"sector_code": S_CODE, "sector_name": "B", "sector_type": S_TYPE}])
    db.expire_all()
    r = db.execute(select(SaSector).where(SaSector.sector_code == S_CODE)).scalar_one()
    assert r.sector_name == "B"


def test_drops_invalid_rows(cleanup):
    rows = [
        {"sector_code": S_CODE, "sector_name": "x", "sector_type": S_TYPE},
        {"sector_code": None, "sector_type": S_TYPE},
        {"sector_code": S_CODE, "sector_type": None},
    ]
    assert sync_sector.upsert_sectors(cleanup, rows) == 1


def test_sync_all_handles_partial_failure(monkeypatch, cleanup):
    """One sector type failing shouldn't abort the other."""
    db = cleanup

    def fake_list(sector_type="industry"):
        if sector_type == "concept":
            raise RuntimeError("concept endpoint down")
        return [{"sector_code": S_CODE, "sector_name": "x", "sector_type": S_TYPE}]

    monkeypatch.setattr(sync_sector.akshare_client, "fetch_sector_list", fake_list)
    n = sync_sector.sync_all(db)
    assert n == 1  # industry written despite concept raising
