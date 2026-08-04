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
    benchmark: str | None = None,
) -> dict:
    """Run a backtest for the FIRST stock in ``stock_pool`` (V1 single-stock).

    V2 adds advanced metrics (calmar/IR/profit-loss-ratio), drawdown/position
    curves, and an optional benchmark comparison (``benchmark`` = ETF code like
    ``510300`` for 沪深300).

    Returns a dict with the headline + advanced metrics plus curves and
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

    # V2: optional benchmark comparison. ``benchmark`` may be either a stock/ETF
    # code present in daily_prices, or an index code from sa_index_quote
    # (e.g. ``sh000001`` 上证指数). We build a close-price series either way.
    benchmark_rows = None
    if benchmark:
        from app.models.market_data import SaIndexQuote

        # Try index table first (codes carry sh/sz prefix).
        idx_rows = db.execute(
            select(SaIndexQuote)
            .where(
                SaIndexQuote.index_code == benchmark,
                SaIndexQuote.trade_date >= start_date,
                SaIndexQuote.trade_date <= end_date,
            )
            .order_by(SaIndexQuote.trade_date.asc())
        ).scalars().all()
        if idx_rows:
            # Build a lightweight list mimicking DailyPrice for _kline_to_dataframe.
            class _IdxRow:
                def __init__(self, r):
                    self.trade_date = r.trade_date
                    self.open = r.open
                    self.close = r.close
                    self.high = r.high
                    self.low = r.low
                    self.volume = None
            benchmark_rows = [_IdxRow(r) for r in idx_rows if r.close is not None]
        else:
            # Fall back to daily_prices (stock/ETF codes).
            benchmark_rows = market_service.get_kline(db, benchmark, start=start_date, end=end_date)

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
    # V2: annualised return (for Calmar) + returns series (for IR vs benchmark).
    cerebro.addanalyzer(
        bt.analyzers.TimeReturn, _name="timereturn_annual",
        timeframe=bt.TimeFrame.Years,
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
    lost = ta.get("lost", {}).get("total", 0) or 0
    trade_count = int(closed)
    win_rate = (won / closed * 100.0) if closed > 0 else 0.0

    # --- equity curve (compound daily returns from TimeReturn) -------------
    tr = strat.analyzers.timereturn.get_analysis()
    equity = cash
    curve: list[dict[str, Any]] = []
    eq_series: list[float] = []
    for d, r in tr.items():
        equity *= 1.0 + float(r)
        eq_series.append(equity)
        d_iso = d.date().isoformat() if hasattr(d, "date") else str(d)
        curve.append({"date": d_iso, "equity": round(equity, 2)})

    # --- V2 advanced metrics ------------------------------------------------
    # Calmar = annualised return / max drawdown.
    annual_ret = None
    tr_annual = strat.analyzers.timereturn_annual.get_analysis()
    if tr_annual:
        annual_ret = float(list(tr_annual.values())[-1]) * 100  # last year's return
    calmar = None
    if annual_ret is not None and max_drawdown > 0:
        calmar = round(annual_ret / max_drawdown, 4)

    # Profit/loss ratio = avg win / avg loss.
    pnl_ratio = None
    avg_win = ta.get("won", {}).get("pnl", {}).get("average")
    avg_lost = ta.get("lost", {}).get("pnl", {}).get("average")
    if avg_win is not None and avg_lost not in (None, 0, 0.0):
        pnl_ratio = round(float(abs(avg_win / avg_lost)), 4)

    # Drawdown curve: running max drawdown from the equity series.
    dd_curve: list[dict] = []
    if eq_series:
        peak = eq_series[0]
        for i, eq in enumerate(eq_series):
            peak = max(peak, eq)
            dd_pct = (peak - eq) / peak * 100 if peak > 0 else 0
            dd_curve.append({"date": curve[i]["date"], "drawdown": round(dd_pct, 4)})

    # Position curve: size held per bar (from the strategy's position). We
    # approximate by sampling the broker position over the timereturn dates.
    pos_curve: list[dict] = []
    for i, (d, _r) in enumerate(tr.items()):
        d_iso = d.date().isoformat() if hasattr(d, "date") else str(d)
        # position size at this bar isn't directly accessible post-run; we
        # derive it from trades: a holding spans entry..exit.
        pos_curve.append({"date": d_iso, "position": 0})
    # Fill positions from the recorded trades (in-market between entry/exit).
    trades_out = list(strat.analyzers.traderecorder.get_analysis())
    for t in trades_out:
        entry, exit_ = t.get("entry_date"), t.get("exit_date")
        size = abs(t.get("size", 0) or 0)
        for p in pos_curve:
            if entry and exit_ and entry <= p["date"] <= exit_:
                p["position"] = size
            elif entry and not exit_ and p["date"] >= entry:
                p["position"] = size

    # Benchmark comparison: compute buy-and-hold return for the benchmark code
    # over the same window, normalised to the same equity curve shape.
    benchmark_curve: list[dict] = []
    benchmark_return = None
    information_ratio = None
    if benchmark_rows:
        bm_df = _kline_to_dataframe(benchmark_rows)
        if not bm_df.empty:
            bm_ret = bm_df["close"].pct_change().dropna()
            bm_equity = cash
            strat_rets = pd.Series([float(r) for r in tr.values()])
            # align by length
            n = min(len(bm_ret), len(strat_rets))
            for i in range(n):
                bm_equity *= 1.0 + float(bm_ret.iloc[i])
                d_iso = (
                    bm_ret.index[i].date().isoformat()
                    if hasattr(bm_ret.index[i], "date")
                    else str(bm_ret.index[i])
                )
                benchmark_curve.append({"date": d_iso, "equity": round(bm_equity, 2)})
            benchmark_return = round((bm_equity - cash) / cash * 100, 4)
            # Information ratio = mean(strat - bm) / std(strat - bm), annualised.
            if n >= 5:
                excess = strat_rets.iloc[:n].values - bm_ret.iloc[:n].values
                std = float(pd.Series(excess).std())
                if std > 0:
                    information_ratio = round(
                        float(pd.Series(excess).mean() / std * (250 ** 0.5)), 4
                    )

    return {
        "return_rate": round(return_rate, 4),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe": round(sharpe, 4),
        "win_rate": round(win_rate, 4),
        "trade_count": trade_count,
        "final_value": round(final_value, 2),
        "equity_curve": curve,
        "trades": trades_out,
        # V2 advanced
        "calmar": calmar,
        "information_ratio": information_ratio,
        "profit_loss_ratio": pnl_ratio,
        "drawdown_curve": dd_curve,
        "position_curve": pos_curve,
        "benchmark_curve": benchmark_curve,
        "benchmark_return": benchmark_return,
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
        # ``benchmark`` is a run-level param, NOT a strategy param — pull it out
        # so it isn't passed to the backtrader Strategy via **params.
        strategy_params = dict(run.params or {})
        benchmark = strategy_params.pop("benchmark", None)
        result_data = run_backtest(
            db,
            strategy=run.strategy,
            params=strategy_params,
            stock_pool=run.stock_pool or [],
            start_date=run.start_date,
            end_date=run.end_date,
            initial_cash=run.initial_cash,
            commission=run.commission,
            slippage=run.slippage,
            benchmark=benchmark,
        )
        result = SaBacktestResult(
            run_id=run.run_id,
            return_rate=result_data["return_rate"],
            max_drawdown=result_data["max_drawdown"],
            sharpe=result_data["sharpe"],
            win_rate=result_data["win_rate"],
            calmar=result_data.get("calmar"),
            information_ratio=result_data.get("information_ratio"),
            profit_loss_ratio=result_data.get("profit_loss_ratio"),
            equity_curve=result_data["equity_curve"],
            drawdown_curve=result_data.get("drawdown_curve"),
            position_curve=result_data.get("position_curve"),
            benchmark_curve=result_data.get("benchmark_curve"),
            benchmark_return=result_data.get("benchmark_return"),
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
