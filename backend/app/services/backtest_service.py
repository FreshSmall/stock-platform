"""Backtest engine: DB K-line -> backtrader Cerebro -> metrics -> persistence.

Flow (single-stock, V1):

    DailyPrice rows -> pandas DataFrame (DatetimeIndex, OHLCV)
                    -> backtrader PandasData feed
                    -> Cerebro + registered Strategy + analyzers
                    -> extract 5 headline metrics + equity curve + trades
                    -> persist to ``sa_backtest_run`` / ``sa_backtest_result``

The 5 headline metrics (return_rate, max_drawdown, sharpe, win_rate,
trade_count) are extracted defensively because backtrader's analyzer outputs
are nested AutoOrderedDicts whose shape varies across runs (e.g. SharpeRatio
returns ``None`` when there are <2 bars). The per-trade list is best-effort:
backtrader's ``TradeAnalyzer`` exposes no clean per-trade record, so a custom
:class:`_TradeRecorder` analyzer captures closed trades via ``notify_trade``.

backtrader API notes (probed against 1.9.78.123 — see commit for the probe
script rationale):

* ``DrawDown`` analysis has NO ``maxdrawdown`` top-level key. The max
  drawdown lives at ``dd.max.drawdown`` (``dd.max`` is itself an
  AutoOrderedDict). ``getattr(dd, "maxdrawdown", 0.0)`` silently returns 0,
  which would hide real drawdowns — we read ``dd.max.drawdown`` explicitly
  with a ``getattr`` fallback for robustness.
* ``TimeReturn`` keys are ``datetime.datetime`` objects (not float bar
  indices), so ``d.date().isoformat()`` gives the equity-curve x-axis.
* The ``Trade`` object exposes ``dtopen`` / ``dtclose`` (float-coded
  datetimes), ``price``, ``pnlcomm`` and ``barlen``. There is NO ``trade.dt``
  and ``trade.history`` is empty unless ``historyon=True``. ``trade.size`` is
  0 once the trade has closed, so we record the entry size on open.
"""

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import backtrader as bt
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.backtest import SaBacktestResult, SaBacktestRun
from app.models.stock import DailyPrice
from app.services import market_service
from app.strategy import registry

logger = logging.getLogger(__name__)

# A backtest needs enough warmup bars for the slowest default indicator (the
# 20-period slow SMA) plus a handful of trading bars; 30 is a safe floor.
_MIN_BARS = 30


class _TradeRecorder(bt.Analyzer):
    """Records closed trades with entry/exit dates, price, size and net pnl.

    backtrader's built-in ``TradeAnalyzer`` only aggregates (won/lost totals,
    streaks, ...); it does not expose a per-trade list. This analyzer hooks
    ``notify_trade`` to collect each CLOSED trade's details.

    We stash the entry size on ``justopened`` because ``trade.size`` is reset
    to 0 once the position closes, so reading it on close would lose the
    signed long/short direction.
    """

    def __init__(self) -> None:
        self.trades: list[dict[str, Any]] = []
        self._entry_size: int = 0

    def notify_trade(self, trade: bt.Trade) -> None:  # type: ignore[override]
        if trade.justopened:
            # Remember the signed size so we can report long/short direction
            # even after the trade closes (trade.size -> 0 on close).
            self._entry_size = trade.size
        if trade.isclosed:
            entry_dt = bt.num2date(trade.dtopen).date() if trade.dtopen else None
            exit_dt = bt.num2date(trade.dtclose).date() if trade.dtclose else None
            self.trades.append(
                {
                    "entry_date": entry_dt.isoformat() if entry_dt else None,
                    "exit_date": exit_dt.isoformat() if exit_dt else None,
                    "price": round(float(trade.price), 4),
                    "size": self._entry_size,
                    "pnl": round(float(trade.pnlcomm), 2),
                    "bars": int(trade.barlen),
                }
            )

    def get_analysis(self) -> list[dict[str, Any]]:
        return self.trades


def _kline_to_dataframe(rows: list[DailyPrice]) -> pd.DataFrame:
    """Convert ``DailyPrice`` rows to a backtrader-friendly DataFrame.

    backtrader's ``PandasData`` feed requires a ``DatetimeIndex`` and OHLCV
    columns. We drop rows whose ``close`` is NULL (incomplete bar), sort
    ascending by date, and coerce the index to ``datetime64`` so ``PandasData``
    accepts it.
    """
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        [
            {
                "date": r.trade_date,
                "open": float(r.open) if r.open is not None else float("nan"),
                "high": float(r.high) if r.high is not None else float("nan"),
                "low": float(r.low) if r.low is not None else float("nan"),
                "close": float(r.close) if r.close is not None else float("nan"),
                "volume": float(r.volume) if r.volume is not None else 0.0,
            }
            for r in rows
        ]
    )
    df = df.dropna(subset=["close"]).sort_values("date")
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    return df


def run_backtest(
    db: Session,
    strategy: str,
    params: dict,
    stock_pool: list[str],
    start_date: date,
    end_date: date,
    initial_cash: Decimal = Decimal("100000"),
    commission: Decimal = Decimal("0.0003"),
    slippage: Decimal = Decimal("0.0001"),
) -> dict:
    """Run a backtest for the FIRST stock in ``stock_pool`` (V1 single-stock).

    Returns a dict with the 5 headline metrics plus ``equity_curve`` and
    ``trades``. Raises ``ValueError`` if the strategy is unknown, the pool is
    empty, there is no K-line data, or there are too few bars.
    """
    meta = registry.get(strategy)
    if meta is None or meta.cls is None:
        raise ValueError(f"unknown or unavailable strategy: {strategy}")

    if not stock_pool:
        raise ValueError("stock_pool is empty")
    code = stock_pool[0]

    rows = market_service.get_kline(db, code, start=start_date, end=end_date)
    if not rows:
        raise ValueError(f"no kline data for {code} in [{start_date}, {end_date}]")

    df = _kline_to_dataframe(rows)
    if len(df) < _MIN_BARS:
        raise ValueError(f"insufficient data ({len(df)} bars) for backtest")

    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(float(initial_cash))
    cerebro.broker.setcommission(commission=float(commission))
    cerebro.broker.set_slippage_perc(perc=float(slippage))
    data = bt.feeds.PandasData(dataname=df)
    cerebro.adddata(data)
    cerebro.addstrategy(meta.cls, **params)

    # Analyzers: the built-ins cover the headline aggregates; the custom
    # _TradeRecorder fills the per-trade list that TradeAnalyzer can't give.
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(
        bt.analyzers.SharpeRatio,
        _name="sharpe",
        timeframe=bt.TimeFrame.Days,
        annualize=True,
    )
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(
        bt.analyzers.TimeReturn, _name="timereturn", timeframe=bt.TimeFrame.Days
    )
    cerebro.addanalyzer(_TradeRecorder, _name="traderecorder")

    results = cerebro.run()
    strat = results[0]

    # --- headline metrics ---------------------------------------------------
    final_value = float(cerebro.broker.getvalue())
    cash = float(initial_cash)
    return_rate = (final_value - cash) / cash * 100.0

    # DrawDown: max drawdown is at dd.max.drawdown (NOT dd.maxdrawdown).
    dd = strat.analyzers.drawdown.get_analysis()
    max_drawdown = float(getattr(getattr(dd, "max", None), "drawdown", 0.0) or 0.0)

    sharpe_raw = strat.analyzers.sharpe.get_analysis().get("sharperatio")
    sharpe = float(sharpe_raw) if sharpe_raw is not None else 0.0

    # TradeAnalyzer: total.total counts open+closed; won.total counts only
    # closed winners. We report trade_count as closed trades (matches the
    # per-trade list length) and win_rate over closed trades.
    ta = strat.analyzers.trades.get_analysis()
    closed = ta.get("total", {}).get("closed", 0) or 0
    won = ta.get("won", {}).get("total", 0) or 0
    trade_count = int(closed)
    win_rate = (won / closed * 100.0) if closed > 0 else 0.0

    # --- equity curve (compound daily returns from TimeReturn) -------------
    tr = strat.analyzers.timereturn.get_analysis()
    equity = cash
    curve: list[dict[str, Any]] = []
    for d, r in tr.items():
        equity *= 1.0 + float(r)
        # TimeReturn keys are datetime.datetime objects in this backtrader
        # version, so .date().isoformat() is safe; fall back to str() for any
        # future version that hands back a float bar index.
        d_iso = d.date().isoformat() if hasattr(d, "date") else str(d)
        curve.append({"date": d_iso, "equity": round(equity, 2)})

    # --- per-trade list (from the custom analyzer) -------------------------
    trades_out = list(strat.analyzers.traderecorder.get_analysis())

    return {
        "return_rate": round(return_rate, 4),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe": round(sharpe, 4),
        "win_rate": round(win_rate, 4),
        "trade_count": trade_count,
        "final_value": round(final_value, 2),
        "equity_curve": curve,
        "trades": trades_out,
    }


def create_backtest_run(db: Session, user_id: int | None, req: dict) -> SaBacktestRun:
    """Insert a ``pending`` run row and return it (not yet executed)."""
    run_id = f"bt-{uuid.uuid4().hex[:12]}"
    run = SaBacktestRun(
        run_id=run_id,
        user_id=user_id,
        strategy=req["strategy"],
        params=req.get("params", {}),
        stock_pool=req["stock_pool"],
        start_date=req["start_date"],
        end_date=req["end_date"],
        initial_cash=req["initial_cash"],
        commission=req.get("commission", Decimal("0.0003")),
        slippage=req.get("slippage", Decimal("0.0001")),
        status="pending",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def execute_and_store(db: Session, run: SaBacktestRun) -> SaBacktestResult:
    """Run the backtest and store the result row; updates ``run.status``.

    On any exception the run is marked ``failed`` with the error message and
    the exception is re-raised so the caller can decide whether to surface it.
    """
    run.status = "running"
    db.commit()
    try:
        result_data = run_backtest(
            db,
            strategy=run.strategy,
            params=run.params or {},
            stock_pool=run.stock_pool or [],
            start_date=run.start_date,
            end_date=run.end_date,
            initial_cash=run.initial_cash,
            commission=run.commission,
            slippage=run.slippage,
        )
        result = SaBacktestResult(
            run_id=run.run_id,
            return_rate=result_data["return_rate"],
            max_drawdown=result_data["max_drawdown"],
            sharpe=result_data["sharpe"],
            win_rate=result_data["win_rate"],
            equity_curve=result_data["equity_curve"],
            trades=result_data["trades"],
        )
        db.add(result)
        run.status = "done"
        run.finished_at = func.now()
        db.commit()
        db.refresh(result)
        return result
    except Exception as e:
        run.status = "failed"
        run.error = str(e)
        db.commit()
        raise


def get_run(db: Session, run_id: str) -> SaBacktestRun | None:
    """Fetch a run row by its business key, or ``None`` if absent."""
    return db.execute(
        select(SaBacktestRun).where(SaBacktestRun.run_id == run_id)
    ).scalar_one_or_none()


def get_result(db: Session, run_id: str) -> SaBacktestResult | None:
    """Fetch the result row for ``run_id``, or ``None`` if absent/failed."""
    return db.execute(
        select(SaBacktestResult).where(SaBacktestResult.run_id == run_id)
    ).scalar_one_or_none()
