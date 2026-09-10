"""Factor query/scoring service (BP-V2-001/004/013).

Wires the :mod:`factor` framework to the DB:
- compute a factor's value series for one stock over a date range;
- compute IC (effectiveness) for a factor across the universe;
- multi-factor weighted scoring → stock ranking.

IC computation pulls the latest stock_pool snapshot as the universe and uses
daily_prices for forward returns. Results are cached in sa_factor_ic so the
same (factor, date, horizon) isn't recomputed.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.factor import ic as ic_mod
from app.factor import multi_factor
from app.factor import registry
from app.factor.multi_factor import FactorWeight
from app.models.factor import SaFactorIc
from app.models.stock import DailyPrice, StockPool
from app.services import factor_panel
from app.services.factor_panel import MarketPanel

logger = logging.getLogger(__name__)

# Universe size for IC: top-N by amount keeps the computation fast (full market
# is 4000+; IC over ~300 most-liquid names is representative and < 10s).
_IC_UNIVERSE = 300


def list_factors(category: str | None = None) -> list[dict]:
    """All registered factors (optionally filtered by category)."""
    factors = registry.by_category(category) if category else registry.all_factors()
    return [
        {"code": f.code, "name": f.name, "category": f.category} for f in factors
    ]


def compute_series(
    db: Session, factor_code: str, stock: str, start: date, end: date
) -> list[dict]:
    """Factor value for one stock over [start, end], one point per trade day."""
    f = registry.get(factor_code)
    if f is None:
        return []
    rows = db.execute(
        select(DailyPrice.trade_date)
        .where(
            DailyPrice.stock_code == stock,
            DailyPrice.trade_date >= start,
            DailyPrice.trade_date <= end,
            DailyPrice.close.is_not(None),
        )
        .order_by(DailyPrice.trade_date.asc())
    ).scalars().all()
    return [
        {"trade_date": d.isoformat(), "value": f.compute(db, stock, d)}
        for d in rows
    ]


def _universe_codes(db: Session, trade_date: date, pool: str = "current") -> list[str]:
    """The IC universe: codes with valid close on/around trade_date.

    ``pool="pit"`` (V2.1) restricts to the point-in-time universe from
    ``sa_stock_lifecycle`` — includes stocks that were listed then but
    delisted since (survivorship-bias fix).
    """
    latest = db.execute(
        select(func.max(DailyPrice.trade_date)).where(
            DailyPrice.trade_date <= trade_date
        )
    ).scalar()
    if latest is None:
        return []

    base_codes: set[str] | None = None
    if pool == "pit":
        from app.services import universe_service

        base_codes = set(universe_service.get_pool_asof(db, trade_date))
        if not base_codes:
            return []

    stmt = (
        select(DailyPrice.stock_code)
        .where(
            DailyPrice.trade_date == latest,
            DailyPrice.close.is_not(None),
            DailyPrice.amount.is_not(None),
        )
        .order_by(DailyPrice.amount.desc())
        .limit(_IC_UNIVERSE * 3 if base_codes is not None else _IC_UNIVERSE)
    )
    if base_codes is not None:
        # PIT pool: widen the candidate scan, then intersect (delisted codes
        # rank low/absent by current amount, so the plain top-N under-samples).
        rows = db.execute(stmt).scalars().all()
        pit_ranked = [c for c in rows if c in base_codes]
        # codes in the PIT pool with bars that day but outside the amount scan
        extra = db.execute(
            select(DailyPrice.stock_code)
            .where(
                DailyPrice.trade_date == latest,
                DailyPrice.close.is_not(None),
                DailyPrice.stock_code.in_(list(base_codes)),
            )
            .order_by(DailyPrice.amount.desc())
            .limit(_IC_UNIVERSE)
        ).scalars().all()
        seen, merged = set(pit_ranked), pit_ranked
        for c in extra:
            if c not in seen:
                merged.append(c)
                seen.add(c)
        return merged[:_IC_UNIVERSE]
    return list(db.execute(stmt).scalars().all())


def compute_ic(
    db: Session,
    factor_code: str,
    trade_date: date,
    horizon: int = 5,
    pool: str = "current",
    exclude_st: bool = False,
    exclude_suspended: bool = False,
    only_tradable: bool = False,
    neutralize: str = "none",
) -> dict | None:
    """IC / IR / layered returns for a factor on one rebalance date.

    For simplicity (and because full-panel IC over 4000 stocks × history is
    heavy), this computes a *single-date* IC: factor snapshot vs forward
    ``horizon``-day returns across the universe. ``ir`` and ``win_rate`` are
    derived from that snapshot's sign agreement.

    V2.1 sample governance: ``pool="pit"`` swaps the universe to the
    point-in-time pool; the three flags drop ST / suspended / untradable
    codes via :mod:`app.services.sample_filters` (defaults keep the exact
    V2 behaviour).
    """
    f = registry.get(factor_code)
    if f is None:
        return None

    # The rebalance date may lack `horizon` forward trading days (the factor
    # page passes the range END as trade_date, which defaults to today).
    # Fall back to the latest settled date that HAS horizon forward days, so
    # the page works out of the box; the effective date is returned in the
    # payload. With fewer than horizon+1 settled dates in total there isn't
    # enough history for any date — the caller reports "insufficient data".
    settled_desc = db.execute(
        select(DailyPrice.trade_date)
        .where(DailyPrice.pct_change.is_not(None))
        .group_by(DailyPrice.trade_date)
        .order_by(DailyPrice.trade_date.desc())
        .limit(horizon + 1)
    ).scalars().all()
    if len(settled_desc) < horizon + 1:
        logger.warning("ic: not enough settled history (%d dates)", len(settled_desc))
        return None
    latest_ok = min(trade_date, settled_desc[-1])
    if latest_ok != trade_date:
        logger.info(
            "ic: trade_date %s lacks %d forward days, falling back to %s",
            trade_date, horizon, latest_ok,
        )
        trade_date = latest_ok

    codes = _universe_codes(db, trade_date, pool=pool)
    filter_meta = None
    if exclude_st or exclude_suspended or only_tradable:
        from app.services import sample_filters

        codes, filter_meta = sample_filters.apply_sample_filters(
            db, codes, trade_date,
            exclude_st=exclude_st,
            exclude_suspended=exclude_suspended,
            only_tradable=only_tradable,
        )
    if len(codes) < 10:
        logger.warning("ic: universe too small (%d), skipping", len(codes))
        return None

    # Warm the per-session caches with bulk queries so the per-code compute
    # loop below stays off the DB — without this each code triggers its own
    # query (~30ms × 300 codes ≈ 10s, the dominant cost of this endpoint).
    from app.factor import cache as fcache
    from app.services import market_service

    if f.category in ("trend", "momentum", "volatility", "volume"):
        market_service.prefetch_kline_windows(db, codes, trade_date)
    if f.category in ("volume", "fundamental"):
        fcache.prefetch_stock_pool(db, codes, trade_date)
    if f.category == "fundamental":
        fcache.prefetch_financial(db, codes)
    if f.category == "sentiment":
        fcache.prefetch_market_tables(db)
        fcache.prefetch_streak(db, codes, trade_date)

    # factor values on the universe
    fv = {}
    for code in codes:
        v = f.compute(db, code, trade_date)
        if v is not None:
            fv[code] = v
    if len(fv) < 10:
        return None

    # forward close: the close `horizon` trade days after trade_date
    fwd_dates = db.execute(
        select(DailyPrice.trade_date)
        .where(DailyPrice.trade_date > trade_date, DailyPrice.close.is_not(None))
        .group_by(DailyPrice.trade_date)
        .order_by(DailyPrice.trade_date.asc())
        .limit(horizon + 1)
    ).scalars().all()
    if len(fwd_dates) < horizon:
        return None
    fwd_date = fwd_dates[horizon - 1]
    base_closes = {
        code: close
        for code, close in db.execute(
            select(DailyPrice.stock_code, DailyPrice.close).where(
                DailyPrice.trade_date == trade_date,
                DailyPrice.stock_code.in_(list(fv.keys())),
            )
        ).all()
        if close is not None
    }
    fwd_closes = {
        code: close
        for code, close in db.execute(
            select(DailyPrice.stock_code, DailyPrice.close).where(
                DailyPrice.trade_date == fwd_date,
                DailyPrice.stock_code.in_(list(fv.keys())),
            )
        ).all()
        if close is not None
    }
    fwd_ret = {
        code: float(fwd_closes[code]) / float(base_closes[code]) - 1.0
        for code in fv
        if code in base_closes and code in fwd_closes and base_closes[code]
    }
    fv_series = pd.Series({c: v for c, v in fv.items() if c in fwd_ret})
    fr_series = pd.Series(fwd_ret)
    if len(fv_series) < 10:
        return None

    if neutralize != "none":
        fv_series = _neutralize_series(db, fv_series, trade_date, neutralize).dropna()
        fr_series = fr_series.reindex(fv_series.index)
        if len(fv_series) < 10:
            return None

    ic_val = ic_mod.single_ic(fv_series, fr_series)
    layered = ic_mod.layered_returns(fv_series, fr_series)
    win_rate = float((fv_series * fr_series > 0).mean()) if len(fv_series) else None

    return {
        "factor_code": factor_code,
        "trade_date": trade_date.isoformat(),
        "horizon": horizon,
        "ic": round(ic_val, 4) if ic_val is not None else None,
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "layered_returns": layered,
        "universe_size": len(fv_series),
        "pool": pool,
        "neutralized": neutralize,
        "sample_filter": filter_meta,
    }


def _normalize_specs(factors: list[dict]) -> list[FactorWeight]:
    """``[{"code","weight","direction"?}]`` → FactorWeight list.

    A negative weight is folded into ``direction=-1`` (historical callers
    like recommend_agent expressed reversal factors via weight sign).
    Zero-weight and unknown-code entries are dropped by the caller.
    """
    specs: list[FactorWeight] = []
    for spec in factors:
        code = spec.get("code")
        if not code:
            continue
        w = float(spec.get("weight", 1.0))
        d = int(spec.get("direction") or 1)
        if w < 0:
            w, d = abs(w), -1
        if w == 0:
            continue
        specs.append(FactorWeight(code=code, weight=w, direction=d))
    return specs


def _neutralize_series(
    db: Session, values: pd.Series, trade_date: date, mode: str
) -> pd.Series:
    """Apply cross-sectional neutralization to one day's factor values.

    ``mode``: ``none`` (pass-through) | ``industry`` | ``industry_mcap``.
    Industry from ``sa_industry_map`` (PIT ≤ trade_date), market cap from the
    latest ``stock_pool`` snapshot ≤ trade_date. Stocks the regressors don't
    cover get NaN residuals — IC/scoring then naturally drops them.
    """
    if mode not in ("industry", "industry_mcap"):
        return values
    from app.services import neutralize as neutralize_mod
    from app.services import universe_service

    codes = list(values.index)
    industries = universe_service.get_industry(db, codes, trade_date)
    ind_series = pd.Series({c: industries.get(c) for c in codes}, dtype=object)

    mcap_series = None
    if mode == "industry_mcap":
        rows = db.execute(
            select(StockPool.stock_code, StockPool.total_mv)
            .where(
                StockPool.stock_code.in_(codes),
                StockPool.trade_date <= trade_date,
                StockPool.total_mv.is_not(None),
            )
            .order_by(StockPool.trade_date.asc())
        ).all()
        # ascending dates → the last write per code is the latest snapshot
        latest_mv = {code: float(mv) for code, mv in rows}
        mcap_series = pd.Series(
            {c: mv for c, mv in latest_mv.items() if mv and mv > 0}, dtype=float
        )

    return neutralize_mod.neutralize_cross_section(
        values, industries=ind_series, log_mktcap=mcap_series
    )


def multi_factor_score(
    db: Session,
    factors: list[dict],  # [{"code","weight","direction"?}, ...]
    trade_date: date,
    universe_size: int = 200,
    pool: str = "current",
    exclude_st: bool = False,
    exclude_suspended: bool = False,
    only_tradable: bool = False,
    top_n: int | None = None,
    min_score: float | None = None,
    neutralize: str = "none",
) -> list[dict]:
    """Weighted multi-factor score → ranked stock list (BP-V2-004 / V2.2 V2).

    Unified pipeline from :mod:`app.factor.multi_factor`: per-factor z-score
    with ±3 clip, median imputation for missing factors, direction applied
    before weighting, weights normalised to a unit sum. Replaces the V2
    sign-via-weight ad-hoc sum.

    V2.2 additions: ``direction`` per factor (negative weight folds to
    direction=-1 for backward compatibility), ``top_n``/``min_score`` on the
    output, and ``neutralize`` (industry / industry_mcap) applied to each
    factor cross-section before z-scoring.

    :return: list of ``{stock_code, score, rank, factors}`` best-first — the
        legacy ``{stock_code, score}`` keys are kept so existing consumers
        (recommend_agent, the factor page) keep working.
    """
    specs = _normalize_specs(factors)
    if not specs:
        return []

    codes = _universe_codes(db, trade_date, pool=pool)
    if exclude_st or exclude_suspended or only_tradable:
        from app.services import sample_filters

        codes, _ = sample_filters.apply_sample_filters(
            db, codes, trade_date,
            exclude_st=exclude_st,
            exclude_suspended=exclude_suspended,
            only_tradable=only_tradable,
        )
    codes = codes[:universe_size]
    if not codes:
        return []

    needed = {fw.code for fw in specs if registry.get(fw.code) is not None}
    # Warm the per-session kline/pool caches so the per-code compute loop
    # below stays off the DB (same rationale as compute_ic).
    from app.factor import cache as fcache
    from app.services import market_service

    market_service.prefetch_kline_windows(db, codes, trade_date)
    fcache.prefetch_stock_pool(db, codes, trade_date)

    # raw factor matrix: code -> {factor_code: value} (no weight applied yet)
    matrix: dict[str, dict[str, float]] = {c: {} for c in codes}
    for fw in specs:
        f = registry.get(fw.code)
        if f is None:
            logger.warning("multi_factor_score: unknown factor %r, skipping", fw.code)
            continue
        for code in codes:
            try:
                v = f.compute(db, code, trade_date)
            except Exception:  # noqa: BLE001 — one bad stock must not kill the run
                v = None
            if v is not None:
                matrix[code][fw.code] = v

    raw = pd.DataFrame(matrix).T
    raw = raw[[c for c in needed if c in raw.columns]]
    if raw.empty or not raw.columns.any():
        return []

    if neutralize != "none":
        for col in raw.columns:
            raw[col] = _neutralize_series(db, raw[col], trade_date, neutralize)

    weights = {fw.code: fw.weight for fw in specs if fw.code in raw.columns}
    directions = {fw.code: fw.direction for fw in specs if fw.code in raw.columns}
    score = multi_factor.composite_score(raw, weights, directions)
    ranked = multi_factor.select_top_n(
        score, raw, n=top_n or universe_size, min_score=min_score
    )
    return [
        {
            "stock_code": r["stock"],
            "score": r["score"],
            "rank": r["rank"],
            "factors": r["factors"],
        }
        for r in ranked
    ]


# --- V2.2 panel-based research (BP-V2.2-002/003) ----------------------------


def _rebalance_dates(
    panel: MarketPanel, start: date, end: date, step: int
) -> list[pd.Timestamp]:
    """Trade dates in [start, end], every ``step``-th one."""
    idx = panel.dates
    in_range = idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]
    return list(in_range[:: max(1, step)])


def _panel_context(
    db: Session,
    factor_code: str,
    start: date,
    data_end: date,
    end: date,
    step: int,
    pool: str,
    universe_size: int | None,
    exclude_st: bool,
    exclude_suspended: bool,
    only_tradable: bool,
) -> tuple | None:
    """Shared setup for panel IC series / layered backtests.

    Returns ``(panel, codes, factor_panel_values, listed_mask, status, reb_dates)``
    or None when there is no data / universe.
    """
    if factor_code not in factor_panel.PANEL_FACTORS:
        raise ValueError(
            f"factor {factor_code!r} is not panel-computable "
            f"(supported: {sorted(factor_panel.PANEL_FACTORS)}); "
            "use the single-date IC endpoint for it"
        )
    panel = factor_panel.load_market_panel(db, start, data_end)
    if panel.close.empty:
        return None
    codes = factor_panel.select_universe(
        db, panel, start, end, pool=pool, universe_size=universe_size
    )
    if not codes:
        return None
    panel = MarketPanel(
        close=panel.close.reindex(columns=codes),
        volume=panel.volume.reindex(columns=codes),
    )
    fp = factor_panel.panel_factor_values(panel, factor_code)
    listed = (
        factor_panel.pit_listed_mask(db, panel, codes) if pool == "pit" else None
    )
    status = (
        factor_panel.load_trade_status(db, panel, codes)
        if (exclude_st or exclude_suspended or only_tradable)
        else None
    )
    reb = _rebalance_dates(panel, start, end, step)
    if not reb:
        return None
    return panel, codes, fp, listed, status, reb


def _iter_scoped_cross_sections(
    db: Session,
    fp: pd.DataFrame,
    codes: list[str],
    reb_dates: list[pd.Timestamp],
    listed: pd.DataFrame | None,
    status,
    exclude_st: bool,
    exclude_suspended: bool,
    only_tradable: bool,
    neutralize: str,
    min_stocks: int,
):
    """Yield ``(date, factor_values)`` cross-sections, scope filters applied.

    Order: factor values → PIT listing → ST/suspended/tradability →
    neutralization (residuals, NaN rows dropped). Dates with fewer than
    ``min_stocks`` survivors are skipped.
    """
    for dt in reb_dates:
        if dt not in fp.index:
            continue
        fv = fp.loc[dt].reindex(codes).dropna()
        if fv.empty:
            continue
        if listed is not None and dt in listed.index:
            ok = listed.loc[dt].reindex(fv.index).fillna(False)
            fv = fv[ok.to_numpy()]
        if status is not None and not fv.empty:
            if exclude_st and status.is_st is not None and dt in status.is_st.index:
                bad = status.is_st.loc[dt].reindex(fv.index).fillna(False)
                fv = fv[~bad.to_numpy()]
            if (
                exclude_suspended
                and status.is_suspended is not None
                and dt in status.is_suspended.index
            ):
                bad = status.is_suspended.loc[dt].reindex(fv.index).fillna(False)
                fv = fv[~bad.to_numpy()]
            if (
                only_tradable
                and status.not_tradable is not None
                and dt in status.not_tradable.index
            ):
                bad = status.not_tradable.loc[dt].reindex(fv.index).fillna(False)
                fv = fv[~bad.to_numpy()]
        if len(fv) < min_stocks:
            continue
        if neutralize != "none":
            fv = _neutralize_series(db, fv, dt.date(), neutralize).dropna()
            if len(fv) < min_stocks:
                continue
        yield dt, fv


def _persist_ic_rows(db: Session, rows: list[dict]) -> int:
    """Upsert per-date IC rows into ``sa_factor_ic`` (idempotent)."""
    if not rows:
        return 0
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    payload = [
        {
            "factor_code": r["factor_code"],
            "trade_date": r["trade_date"],
            "horizon": r["horizon"],
            "pool": r["pool"],
            "neutralized": r["neutralized"],
            "ic": r["ic"],
            "ir": None,  # per-series statistic — stored nowhere per-row
            "win_rate": r["win_rate"],
        }
        for r in rows
        if r["ic"] is not None
    ]
    if not payload:
        return 0
    stmt = mysql_insert(SaFactorIc).values(payload)
    stmt = stmt.on_duplicate_key_update(
        ic=stmt.inserted.ic, win_rate=stmt.inserted.win_rate
    )
    db.execute(stmt)
    db.commit()
    return len(payload)


def compute_ic_series(
    db: Session,
    factor_code: str,
    start: date,
    end: date,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
    step: int = 5,
    pool: str = "current",
    exclude_st: bool = False,
    exclude_suspended: bool = False,
    only_tradable: bool = False,
    neutralize: str = "none",
    universe_size: int | None = 800,
    min_stocks: int = 30,
    persist: bool = True,
) -> dict | None:
    """RankIC across many rebalance dates, per horizon (BP-V2.2-002 / T2.3).

    Vectorized over a date × stock panel. For every ``step``-th trade date in
    [start, end] the factor cross-section (PIT/sample/neutralize scoped) is
    correlated against forward ``horizon``-day returns; results are
    aggregated per horizon (mean IC / ICIR / win-rate / decay) and per year,
    and upserted into ``sa_factor_ic`` under the (pool, neutralized) scope.

    :return: ``{series, summary, by_year, persisted, meta}`` or None when the
        range has no usable data.
    """
    horizons = tuple(sorted({int(h) for h in horizons if 1 <= int(h) <= 60}))
    if not horizons:
        raise ValueError("horizons must be 1..60 trade days")
    max_h = max(horizons)
    # forward-return buffer beyond `end` (trading days ≈ 1.45 × calendar)
    data_end = end + timedelta(days=int(max_h * 1.6) + 10)

    ctx = _panel_context(
        db, factor_code, start, data_end, end, step, pool, universe_size,
        exclude_st, exclude_suspended, only_tradable,
    )
    if ctx is None:
        return None
    panel, codes, fp, listed, status, reb_dates = ctx

    fwd = {
        h: factor_panel.forward_return_panel(panel.close, h) for h in horizons
    }

    rows: list[dict] = []
    for dt, fv in _iter_scoped_cross_sections(
        db, fp, codes, reb_dates, listed, status,
        exclude_st, exclude_suspended, only_tradable, neutralize, min_stocks,
    ):
        for h in horizons:
            if dt not in fwd[h].index:
                continue
            fr = fwd[h].loc[dt].reindex(fv.index).dropna()
            common = fv.index.intersection(fr.index)
            if len(common) < min_stocks:
                continue
            ic_val = ic_mod.single_ic(fv.loc[common], fr.loc[common])
            win_rate = (
                float((fv.loc[common] * fr.loc[common] > 0).mean())
                if len(common)
                else None
            )
            rows.append(
                {
                    "trade_date": dt.date(),
                    "horizon": h,
                    "ic": round(ic_val, 4) if ic_val is not None else None,
                    "win_rate": round(win_rate, 4) if win_rate is not None else None,
                    "n": len(common),
                }
            )

    if not rows:
        return None

    frame = pd.DataFrame(rows)
    summary: dict[str, dict] = {}
    for h, grp in frame.groupby("horizon"):
        ic_seq = grp["ic"].dropna()
        summary[str(h)] = {
            "mean_ic": round(float(ic_seq.mean()), 4) if len(ic_seq) else None,
            "ic_std": round(float(ic_seq.std()), 4) if len(ic_seq) > 1 else None,
            "icir": ic_mod.information_ratio(ic_seq),
            "win_rate": round(float((ic_seq > 0).mean()), 4)
            if len(ic_seq)
            else None,
            "n_dates": int(len(ic_seq)),
        }
    frame["year"] = pd.to_datetime(frame["trade_date"]).dt.year
    by_year: dict[str, dict[str, float | None]] = {}
    for (yr, h), grp in frame.groupby(["year", "horizon"]):
        by_year.setdefault(str(yr), {})[str(h)] = (
            round(float(grp["ic"].dropna().mean()), 4)
            if grp["ic"].notna().any()
            else None
        )

    persisted = 0
    if persist:
        persisted = _persist_ic_rows(
            db,
            [
                {
                    "factor_code": factor_code,
                    "trade_date": r["trade_date"],
                    "horizon": r["horizon"],
                    "pool": pool,
                    "neutralized": neutralize,
                    "ic": r["ic"],
                    "win_rate": r["win_rate"],
                }
                for r in rows
            ],
        )

    return {
        "factor_code": factor_code,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "horizons": list(horizons),
        "step": step,
        "pool": pool,
        "neutralized": neutralize,
        "series": [
            {
                "trade_date": r["trade_date"].isoformat(),
                "horizon": int(r["horizon"]),
                "ic": r["ic"],
                "n": int(r["n"]),
            }
            for r in rows
        ],
        "summary": summary,
        "by_year": by_year,
        "persisted_rows": persisted,
    }


def layered_backtest(
    db: Session,
    factor_code: str,
    start: date,
    end: date,
    step: int = 5,
    n_layers: int = 5,
    pool: str = "current",
    exclude_st: bool = False,
    exclude_suspended: bool = False,
    only_tradable: bool = False,
    neutralize: str = "none",
    universe_size: int | None = 800,
    min_stocks: int = 50,
) -> dict | None:
    """N-quantile equal-weight portfolio NAVs + long-short spread (T2.3).

    At each rebalance date the scoped factor cross-section is cut into
    ``n_layers`` quantiles (layer 1 = lowest factor value); every layer holds
    its members equal-weight for the next ``step`` trade days. NAVs compound
    per layer; the long-short spread compounds (top layer − bottom layer)
    per period.
    """
    data_end = end + timedelta(days=int(step * 1.6) + 10)
    ctx = _panel_context(
        db, factor_code, start, data_end, end, step, pool, universe_size,
        exclude_st, exclude_suspended, only_tradable,
    )
    if ctx is None:
        return None
    panel, codes, fp, listed, status, reb_dates = ctx

    hold = factor_panel.forward_return_panel(panel.close, step)

    period_returns: dict[int, list[float]] = {L: [] for L in range(1, n_layers + 1)}
    counts: dict[int, list[int]] = {L: [] for L in range(1, n_layers + 1)}
    used_dates: list[str] = []
    ls_returns: list[float] = []

    for dt, fv in _iter_scoped_cross_sections(
        db, fp, codes, reb_dates, listed, status,
        exclude_st, exclude_suspended, only_tradable, neutralize, min_stocks,
    ):
        if dt not in hold.index:
            continue
        hr = hold.loc[dt].reindex(fv.index).dropna()
        common = fv.index.intersection(hr.index)
        if len(common) < n_layers * 3:
            continue
        layered = ic_mod.layered_returns(fv.loc[common], hr.loc[common], n_layers)
        if not layered or len(layered) < n_layers:
            continue
        by_layer = {item["layer"]: item for item in layered}
        used_dates.append(dt.date().isoformat())
        for L in range(1, n_layers + 1):
            item = by_layer.get(L)
            if item and item["mean_return"] is not None:
                period_returns[L].append(item["mean_return"])
                counts[L].append(item["count"])
        top, bottom = by_layer.get(n_layers), by_layer.get(1)
        if top and bottom and None not in (top["mean_return"], bottom["mean_return"]):
            ls_returns.append(top["mean_return"] - bottom["mean_return"])

    if not used_dates:
        return None

    periods_per_year = factor_panel.TRADING_DAYS_PER_YEAR / step

    def _stats(rets: list[float], nav: list[float]) -> dict:
        import numpy as np

        nav_arr = np.array(nav)
        peak = np.maximum.accumulate(nav_arr)
        dd = float((nav_arr / peak - 1.0).min()) if len(nav_arr) else None
        total = nav_arr[-1] - 1.0 if len(nav_arr) else None
        n_years = len(rets) / periods_per_year if periods_per_year else 0
        ann = float((nav_arr[-1]) ** (1 / n_years) - 1.0) if n_years > 0 else None
        vol = (
            float(np.std(rets, ddof=1) * (periods_per_year**0.5))
            if len(rets) > 1
            else None
        )
        return {
            "total_return": round(total, 4) if total is not None else None,
            "ann_return": round(ann, 4) if ann is not None else None,
            "vol": round(vol, 4) if vol is not None else None,
            "max_drawdown": round(dd, 4) if dd is not None else None,
        }

    def _nav(rets: list[float]) -> list[float]:
        nav, cur = [], 1.0
        for r in rets:
            cur *= 1.0 + r
            nav.append(round(cur, 6))
        return nav

    layers_out = []
    for L in range(1, n_layers + 1):
        rets = period_returns[L]
        layers_out.append(
            {
                "layer": L,
                "nav": _nav(rets),
                "avg_count": round(float(np.mean(counts[L])), 1) if counts[L] else None,
                **_stats(rets, _nav(rets)),
            }
        )

    return {
        "factor_code": factor_code,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "step": step,
        "n_layers": n_layers,
        "pool": pool,
        "neutralized": neutralize,
        "rebalance_dates": used_dates,
        "layers": layers_out,
        "long_short": {
            "nav": _nav(ls_returns),
            **_stats(ls_returns, _nav(ls_returns)),
        },
    }
