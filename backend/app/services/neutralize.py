"""Cross-sectional neutralization (V2.1 BP-V2.1-006 / D6).

The standard factor-hygiene transform the step1 report called for: regress
each day's factor cross-section on industry dummies + log market cap and keep
the residuals, so reported factor returns stop smuggling in industry / size
exposure. Deliberately a pure-function module — V2.2 T2.4 wires it into the
IC / layered / backtest paths behind product switches.
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def neutralize_cross_section(
    values: pd.Series,
    industries: pd.Series | None = None,
    log_mktcap: pd.Series | None = None,
) -> pd.Series:
    """Residualize ``values`` on industry dummies and/or log market cap.

    All series share the index (stock codes); ``values`` is one day's factor
    cross-section. Industries become one-hot dummies (drop_first to avoid the
    dummy trap). Rows with missing regressors are dropped from the fit and
    get ``NaN`` residuals back — the caller decides how to treat them
    (industry coverage gaps shouldn't silently shift everyone's residual).

    Returns residuals with the ORIGINAL index; entries that could not be fit
    are NaN. Raises ``ValueError`` when neither regressor is provided or the
    design matrix is degenerate after alignment.
    """
    if industries is None and log_mktcap is None:
        raise ValueError("neutralize_cross_section needs at least one regressor")

    frame = pd.DataFrame({"v": values.astype(float)})
    if industries is not None:
        frame["ind"] = industries
    if log_mktcap is not None:
        frame["mcap"] = np.log(log_mktcap.astype(float).clip(lower=1e-6))

    cols: list[str] = []
    X_parts: list[pd.DataFrame] = []
    if "ind" in frame.columns:
        dummies = pd.get_dummies(frame["ind"], drop_first=True, dtype=float)
        # single-industry cross-section → dummies are all-zero columns
        dummies = dummies.loc[:, dummies.any()]
        if not dummies.empty:
            X_parts.append(dummies)
            cols.extend(dummies.columns.tolist())
    if "mcap" in frame.columns:
        X_parts.append(frame[["mcap"]])
        cols.append("mcap")
    if not cols:
        # Regressors provided but degenerate (e.g. one industry, no mktcap):
        # fall back to intercept-only = demeaning rather than failing — the
        # caller still gets a usable, well-defined residual series.
        logger.warning("neutralize: regressors degenerate, demeaning only")
        return values - values.mean()

    X = pd.concat(X_parts, axis=1)
    if "ind" in frame.columns:
        # Unknown-industry rows cannot be classified — mask them out of the
        # fit (get_dummies would otherwise treat them as the baseline group)
        # so their residuals come back NaN for the caller to handle.
        X = X.where(~frame["ind"].isna(), np.nan)
    X.insert(0, "const", 1.0)

    # Complete cases only: NaN in value or any regressor drops the row.
    aligned = frame[["v"]].join(X, how="inner").dropna()
    if len(aligned) < len(X.columns):
        # Fewer complete rows than design columns — the fit is underdetermined
        # (lstsq would still answer, but with zero residual dof; not honest).
        logger.warning(
            "neutralize: too few complete rows (%d vs %d columns)",
            len(aligned), len(X.columns),
        )
        return pd.Series(np.nan, index=values.index, name=values.name)

    beta, *_ = np.linalg.lstsq(aligned[X.columns].to_numpy(), aligned["v"].to_numpy(), rcond=None)
    resid = aligned["v"] - aligned[X.columns].to_numpy() @ beta

    out = pd.Series(np.nan, index=values.index, name=values.name)
    out.loc[aligned.index] = resid
    return out
