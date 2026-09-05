"""Factor router (BP-V2-001/004/013). Endpoints under ``/api/v1/factor``."""

from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.services import factor_service

router = APIRouter(prefix="/factor", tags=["factor"])


def _ok(data=None, msg: str = "ok") -> dict:
    from app.main import api_ok

    return api_ok(data, msg)


@router.get("")
def list_factors(
    category: str | None = Query(None, pattern="^(trend|momentum|volatility|volume|fundamental|sentiment)$"),
) -> dict:
    """List all factors, optionally filtered by category."""
    return _ok(factor_service.list_factors(category))


@router.get("/{code}/compute")
def compute_series(
    code: str,
    stock: str = Query(...),
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Factor value series for one stock over a date range."""
    if end is None:
        end = date.today()
    if start is None:
        start = end.replace(year=end.year - 1)
    return _ok(factor_service.compute_series(db, code, stock, start, end))


@router.get("/{code}/ic")
def compute_ic(
    code: str,
    horizon: int = Query(5, ge=1, le=60),
    trade_date: date | None = None,
    pool: str = Query("current", pattern="^(current|pit)$"),
    exclude_st: bool = Query(False),
    exclude_suspended: bool = Query(False),
    only_tradable: bool = Query(False),
    neutralize: str = Query("none", pattern="^(none|industry|industry_mcap)$"),
    db: Session = Depends(get_db),
) -> dict:
    """IC analysis for a factor on one rebalance date (latest if omitted).

    V2.1 sample governance: ``pool=pit`` uses the point-in-time universe;
    the three flags drop ST / suspended / unbuyable codes (defaults = V2 behaviour).
    V2.2: ``neutralize`` residualizes the cross-section on industry dummies
    (+ log market cap) before the IC.
    """
    if trade_date is None:
        trade_date = date.today()
    data = factor_service.compute_ic(
        db, code, trade_date, horizon,
        pool=pool,
        exclude_st=exclude_st,
        exclude_suspended=exclude_suspended,
        only_tradable=only_tradable,
        neutralize=neutralize,
    )
    if data is None:
        return _ok(None, msg="insufficient data")
    return _ok(data)


@router.get("/{code}/ic-series")
def compute_ic_series(
    code: str,
    start: date | None = None,
    end: date | None = None,
    horizons: str = Query("1,5,10,20", description="逗号分隔的持有期（交易日）"),
    step: int = Query(5, ge=1, le=60, description="调仓步长（交易日）"),
    pool: str = Query("current", pattern="^(current|pit)$"),
    exclude_st: bool = Query(False),
    exclude_suspended: bool = Query(False),
    only_tradable: bool = Query(False),
    neutralize: str = Query("none", pattern="^(none|industry|industry_mcap)$"),
    universe_size: int = Query(800, ge=50, le=4600),
    persist: bool = Query(True),
    db: Session = Depends(get_db),
) -> dict:
    """RankIC across rebalance dates, per horizon (V2.2 BP-V2.2-002).

    Panel-computable factors only (price/volume derived); snapshot factors
    (pe/pb/…) use the single-date ``/ic`` route. Results are upserted into
    ``sa_factor_ic`` under the (pool, neutralized) scope unless
    ``persist=false``.
    """
    from datetime import timedelta as _td

    from app.services import factor_panel

    if code not in factor_panel.PANEL_FACTORS:
        return _ok(None, msg=f"因子 {code} 暂不支持时序 IC（需面板化），请使用单日 IC 接口")
    if end is None:
        end = date.today()
    if start is None:
        start = end - _td(days=365)
    try:
        hs = tuple(int(x) for x in horizons.split(",") if x.strip())
    except ValueError:
        return _ok(None, msg="horizons 需为逗号分隔的整数")
    try:
        data = factor_service.compute_ic_series(
            db, code, start, end,
            horizons=hs, step=step, pool=pool,
            exclude_st=exclude_st,
            exclude_suspended=exclude_suspended,
            only_tradable=only_tradable,
            neutralize=neutralize,
            universe_size=universe_size,
            persist=persist,
        )
    except ValueError as e:
        return _ok(None, msg=str(e))
    if data is None:
        return _ok(None, msg="insufficient data")
    return _ok(data)


@router.post("/{code}/layered-backtest")
def layered_backtest(
    code: str,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    """N-quantile equal-weight NAVs + long-short spread (V2.2 BP-V2.2-002).

    Body: ``{"start": "2025-09-01", "end": "2026-09-01", "step": 5,
    "n_layers": 5, "pool": "current|pit", "exclude_st": false,
    "exclude_suspended": false, "only_tradable": false,
    "neutralize": "none|industry|industry_mcap", "universe_size": 800}``.
    """
    from datetime import timedelta as _td

    from app.services import factor_panel

    if code not in factor_panel.PANEL_FACTORS:
        return _ok(None, msg=f"因子 {code} 暂不支持分层回测（需面板化）")
    td_start = payload.get("start")
    td_end = payload.get("end")
    start = date.fromisoformat(td_start) if td_start else date.today() - _td(days=365)
    end = date.fromisoformat(td_end) if td_end else date.today()
    try:
        data = factor_service.layered_backtest(
            db, code, start, end,
            step=int(payload.get("step", 5)),
            n_layers=int(payload.get("n_layers", 5)),
            pool=payload.get("pool", "current"),
            exclude_st=bool(payload.get("exclude_st", False)),
            exclude_suspended=bool(payload.get("exclude_suspended", False)),
            only_tradable=bool(payload.get("only_tradable", False)),
            neutralize=payload.get("neutralize", "none"),
            universe_size=int(payload.get("universe_size", 800)),
        )
    except ValueError as e:
        return _ok(None, msg=str(e))
    if data is None:
        return _ok(None, msg="insufficient data")
    return _ok(data)


@router.get("/presets")
def list_presets() -> dict:
    """Research-validated multi-factor presets (V2.2 BP-V2.2-001)."""
    from app.factor.multi_factor import preset_meta

    return _ok(preset_meta())


@router.post("/score")
def multi_factor_score(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    """Multi-factor weighted scoring → ranked stock list (BP-V2-004 / V2.2 V2).

    Body: ``{"factors": [{"code": "pe", "weight": 1.0, "direction": -1}, ...],
    "preset": "v2_reversal", "trade_date": "2026-07-28", "top_n": 20,
    "min_score": null, "universe_size": 200, "pool": "current|pit",
    "exclude_st": false, "exclude_suspended": false, "only_tradable": false,
    "neutralize": "none|industry|industry_mcap"}`` — ``direction=-1`` prefers
    low values; a negative ``weight`` folds to ``direction=-1`` (V2
    compatibility). ``preset`` is used when ``factors`` is empty. The
    sample-governance keys are optional and default to the unchanged V2
    behaviour.
    """
    factors = payload.get("factors", [])
    preset = payload.get("preset")
    if not factors and preset:
        from app.factor.multi_factor import resolve_preset

        specs = resolve_preset(str(preset))
        if specs is None:
            return _ok(None, msg=f"unknown preset: {preset}")
        factors = [
            {"code": fw.code, "weight": fw.weight, "direction": fw.direction}
            for fw in specs
        ]
    td = payload.get("trade_date")
    trade_date = date.fromisoformat(td) if td else date.today()
    if not factors:
        return _ok(None, msg="no factors specified")
    return _ok(
        factor_service.multi_factor_score(
            db, factors, trade_date,
            universe_size=int(payload.get("universe_size", 200)),
            pool=payload.get("pool", "current"),
            exclude_st=bool(payload.get("exclude_st", False)),
            exclude_suspended=bool(payload.get("exclude_suspended", False)),
            only_tradable=bool(payload.get("only_tradable", False)),
            top_n=payload.get("top_n"),
            min_score=payload.get("min_score"),
            neutralize=payload.get("neutralize", "none"),
        )
    )


@router.post("/portfolio-backtest")
def portfolio_backtest(payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    """Multi-factor portfolio backtest (V2.2 BP-V2.2-005 / T2.5).

    Body: ``{"preset": "v2_reversal"}`` or ``{"factors": [{"code","weight",
    "direction"}, ...]}`` plus ``{"start", "end", "freq": "W"|"M"|<N>,
    "top_n": 10, "initial_cash": 100000, "benchmark": "sh000001",
    "pool": "current|pit", "exclude_st", "exclude_suspended",
    "only_tradable", "neutralize", "liquidity_top_k": 1000, "cost": {...}}``.

    Synchronous; returns the full payload (metrics / nav / turnover /
    holdings / rebalances) with a ``run_id`` queryable via
    ``GET /backtest/{run_id}``.
    """
    from fastapi import HTTPException

    from app.services import portfolio_backtest_service as pbs

    td_start = payload.get("start")
    td_end = payload.get("end")
    try:
        data = pbs.run_mf_backtest(
            db,
            factors=payload.get("factors"),
            preset=payload.get("preset"),
            start=date.fromisoformat(td_start) if td_start else date.today() - timedelta(days=365),
            end=date.fromisoformat(td_end) if td_end else date.today(),
            freq=str(payload.get("freq", "W")),
            top_n=int(payload.get("top_n", 10)),
            initial_cash=float(payload.get("initial_cash", 100_000)),
            benchmark=payload.get("benchmark", "sh000001"),
            pool=payload.get("pool", "current"),
            exclude_st=bool(payload.get("exclude_st", False)),
            exclude_suspended=bool(payload.get("exclude_suspended", False)),
            only_tradable=bool(payload.get("only_tradable", True)),
            neutralize=payload.get("neutralize", "none"),
            liquidity_top_k=int(payload.get("liquidity_top_k", 1000)),
            cost=payload.get("cost"),
            user_id=None,
        )
    except ValueError as e:
        return _ok(None, msg=str(e))
    except Exception as e:  # noqa: BLE001 — surface engine errors as msg
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _ok(data)
