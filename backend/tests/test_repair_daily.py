"""Tests for app.data.repair_daily detection + admin long-task execution."""

import time

import pandas as pd
import pytest
from sqlalchemy import delete

from app.data import repair_daily
from app.models.kline import SaAdjustFactor, SaKlineDaily
from app.services import admin_service


# ---------------------------------------------------------------------------
# Detection pure functions
# ---------------------------------------------------------------------------

def _frame(closes: list[float], pcts: list[float | None]) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"close": closes, "pct_change": pcts}, index=dates
    )


class TestDetection:
    def test_frozen_detected(self):
        # 5 flat bars mid-series → one segment
        closes = [10.0, 10.5, 11.0, 11.0, 11.0, 11.0, 11.0, 12.0, 12.5]
        pcts = [None, 5.0, 4.8, 0.0, 0.0, 0.0, 0.0, 9.1, 4.2]
        segs = repair_daily.frozen_segments(_frame(closes, pcts))
        assert len(segs) == 1
        start, end, n = segs[0]
        assert n == 5
        assert end > start

    def test_flat_but_moving_pct_not_frozen(self):
        # close unchanged but pct claims moves (data inconsistency of the
        # OTHER kind) — not a freeze by our definition.
        closes = [11.0] * 8
        pcts = [None] + [1.0] * 7
        assert repair_daily.frozen_segments(_frame(closes, pcts)) == []

    def test_short_flat_run_not_frozen(self):
        closes = [10.0, 11.0, 11.0, 11.0, 11.0, 12.0]  # only 4 flat bars
        pcts = [None, 10.0, 0.0, 0.0, 0.0, 8.3]
        assert repair_daily.frozen_segments(_frame(closes, pcts)) == []

    def test_misaligned_detected(self):
        # 60 normal bars at ~10, then a sustained 0.5元 segment (600066 shape)
        closes = [10.0 + (i % 5) * 0.1 for i in range(60)] + [0.5] * 10
        pcts = [None] + [1.0] * (len(closes) - 1)
        segs = repair_daily.misaligned_segments(_frame(closes, pcts))
        assert len(segs) == 1
        assert segs[0][2] == 10

    def test_normal_series_clean(self):
        closes = [10.0 + (i % 5) * 0.1 for i in range(80)]
        pcts = [None] + [1.0] * 79
        assert repair_daily.misaligned_segments(_frame(closes, pcts)) == []


# ---------------------------------------------------------------------------
# Admin long-task round trip
# ---------------------------------------------------------------------------

class TestLongTask:
    def test_run_task_async_lifecycle(self, monkeypatch):
        """Submit → running row → background success → get_run sees result."""
        admin_service._register_runners()
        monkeypatch.setitem(admin_service._TASK_RUNNERS, "daily_k_repair", lambda: 7)

        run_id = admin_service.run_task_async("daily_k_repair", triggered_by="manual:test")
        assert isinstance(run_id, int)

        # Poll for the background worker to finish (single-worker executor).
        deadline = time.time() + 15
        run = None
        while time.time() < deadline:
            run = admin_service.get_run(run_id)
            if run["status"] != "running":
                break
            time.sleep(0.1)
        assert run is not None and run["status"] == "success"
        assert run["rows_affected"] == 7
        assert run["triggered_by"] == "manual:test"

    def test_run_task_async_rejects_non_long(self):
        admin_service._register_runners()
        with pytest.raises(ValueError):
            admin_service.run_task_async("pool_sync", triggered_by="manual:test")
