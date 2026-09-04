"""V2.1 adjust-basis tests for market_service.get_kline (spec-004 B4).

Live DB + sentinel stock code ``ZZT21`` in the V2.1 tables; the legacy
``daily_prices`` path is asserted structurally (model switch) only, since the
rest of the suite already covers legacy behaviour.
"""

from datetime import date

import pytest
from sqlalchemy import delete, select

from app.core.config import settings
from app.models.kline import SaAdjustFactor, SaKlineDaily
from app.models.stock import DailyPrice
from app.services import market_service as ms

SENTINEL = "ZZT21"

D1, D2, D3, D4 = (date(2026, 6, 1 + i) for i in range(4))
# dividend on D3: raw 101→96 (-4.95%) but true pct -1% → factor jumps ×1.0416…
JUMP = 0.99 / (96.0 / 101.0)


@pytest.fixture
def cleanup_sentinel(db_session, monkeypatch):
    monkeypatch.setattr(settings, "kline_source", "legacy")
    for model in (SaKlineDaily, SaAdjustFactor):
        db_session.execute(delete(model).where(model.stock_code == SENTINEL))
        db_session.commit()
    try:
        yield db_session
    finally:
        for model in (SaKlineDaily, SaAdjustFactor):
            db_session.execute(delete(model).where(model.stock_code == SENTINEL))
            db_session.commit()


def _seed_v2(db) -> None:
    bars = [
        (D1, 100.0, None),
        (D2, 101.0, 1.0),
        (D3, 96.0, -1.0),
        (D4, 97.0, 1.04),
    ]
    for d, c, p in bars:
        db.add(SaKlineDaily(stock_code=SENTINEL, trade_date=d, open=c, close=c,
                            high=c, low=c, volume=100, amount=c * 100,
                            pct_change=p, turnover=0.1, source="tencent"))
    factors = [(D1, 1.0), (D2, 1.0), (D3, JUMP), (D4, JUMP)]
    for d, f in factors:
        db.add(SaAdjustFactor(stock_code=SENTINEL, trade_date=d, adj_factor=f, anchored=1))
    db.commit()


def test_source_switch_defaults_to_legacy():
    assert ms._is_v2() is False
    assert ms._kline_model() is DailyPrice


def test_hfq_basis_and_return_consistency(cleanup_sentinel, monkeypatch):
    """hfq closes: D3/D2 return == true pct (-1%) despite the raw dividend gap."""
    db = cleanup_sentinel
    _seed_v2(db)
    monkeypatch.setattr(settings, "kline_source", "v2")
    # clear any session cache entries from seeding
    setattr(db, ms._KLINE_CACHE_ATTR, {})

    bars = ms.get_kline(db, SENTINEL, adjust="hfq")
    closes = [float(b.close) for b in bars]
    assert closes[0] == pytest.approx(100.0, abs=0.01)
    assert closes[2] == pytest.approx(96.0 * JUMP, abs=0.01)
    # the whole point: hfq close-to-close == true return on the div day
    assert closes[2] / closes[1] - 1 == pytest.approx(-0.01, abs=1e-4)


def test_qfq_basis_anchored_to_end(cleanup_sentinel, monkeypatch):
    """qfq re-bases on the latest factor AS OF end — no look-ahead."""
    db = cleanup_sentinel
    _seed_v2(db)
    monkeypatch.setattr(settings, "kline_source", "v2")
    setattr(db, ms._KLINE_CACHE_ATTR, {})

    # end=D2 → factor_latest = 1.0 → D1/D2 unchanged (display view "then")
    bars_then = ms.get_kline(db, SENTINEL, end=D2, adjust="qfq")
    assert float(bars_then[0].close) == pytest.approx(100.0, abs=0.01)

    # end=D4 → factor_latest = JUMP → all bars scaled by 1/JUMP
    bars_now = ms.get_kline(db, SENTINEL, end=D4, adjust="qfq")
    assert float(bars_now[0].close) == pytest.approx(100.0 / JUMP, abs=0.01)
    assert float(bars_now[3].close) == pytest.approx(97.0, abs=0.01)  # latest bar ≈ raw


def test_raw_passthrough_and_cache_stays_raw(cleanup_sentinel, monkeypatch):
    """Same session: hfq call must NOT poison the cache for a later raw call."""
    db = cleanup_sentinel
    _seed_v2(db)
    monkeypatch.setattr(settings, "kline_source", "v2")
    setattr(db, ms._KLINE_CACHE_ATTR, {})

    hfq = ms.get_kline(db, SENTINEL, adjust="hfq")
    assert float(hfq[0].close) == pytest.approx(100.0, abs=0.01)
    raw = ms.get_kline(db, SENTINEL, adjust="raw")
    assert float(raw[0].close) == pytest.approx(100.0, abs=0.01)
    assert float(raw[2].close) == pytest.approx(96.0, abs=0.01)  # raw stored value

    # and the cache itself holds ORM rows, not AdjustedBar copies
    entry = ms._kline_cache(db)[SENTINEL]
    assert all(not isinstance(r, ms.AdjustedBar) for r in entry[2])


def test_v2_no_factor_rows_passthrough(cleanup_sentinel, monkeypatch):
    """Index codes / not-yet-rebuilt stocks: no factors → bars as-is (ratio 1)."""
    db = cleanup_sentinel
    _seed_v2(db)
    db.execute(delete(SaAdjustFactor).where(SaAdjustFactor.stock_code == SENTINEL))
    db.commit()
    monkeypatch.setattr(settings, "kline_source", "v2")
    setattr(db, ms._KLINE_CACHE_ATTR, {})

    bars = ms.get_kline(db, SENTINEL, adjust="hfq")
    assert float(bars[2].close) == pytest.approx(96.0, abs=0.01)


def test_weekly_aggregation_applies_adjust(cleanup_sentinel, monkeypatch):
    """The w/m branch folds the ratio in before resample (D1-D5 same week)."""
    db = cleanup_sentinel
    _seed_v2(db)
    # add D5 so the week bucket has a second bar
    db.add(SaKlineDaily(stock_code=SENTINEL, trade_date=date(2026, 6, 5), open=98.0,
                        close=98.0, high=98.0, low=98.0, volume=10, amount=9800.0,
                        pct_change=1.03, turnover=0.1, source="tencent"))
    db.add(SaAdjustFactor(stock_code=SENTINEL, trade_date=date(2026, 6, 5),
                          adj_factor=JUMP, anchored=1))
    db.commit()
    monkeypatch.setattr(settings, "kline_source", "v2")
    setattr(db, ms._KLINE_CACHE_ATTR, {})

    bars = ms.get_kline(db, SENTINEL, period="w", adjust="hfq")
    assert bars, "weekly bars should exist for the seeded week"
    assert float(bars[0]["open"]) == pytest.approx(100.0, abs=0.01)
    assert float(bars[0]["close"]) == pytest.approx(98.0 * JUMP, abs=0.01)
