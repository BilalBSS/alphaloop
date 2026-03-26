# / comprehensive risk metrics
# / VaR (parametric, historical, MC), CVaR, max drawdown, EVT tail estimation
# / canonical max_drawdown lives here — backtest.py should import from this module

from __future__ import annotations

import math

import numpy as np
from scipy import stats
import structlog

logger = structlog.get_logger(__name__)


def var_parametric(
    returns: np.ndarray,
    confidence: float = 0.95,
    distribution: str = "normal",
) -> float:
    # / parametric VaR assuming normal or student-t distribution
    # / returns positive number representing potential loss
    returns = np.asarray(returns, dtype=np.float64)
    clean = returns[~np.isnan(returns)]

    if len(clean) == 0:
        raise ValueError("returns must not be empty")
    if len(clean) < 2:
        raise ValueError(f"need at least 2 observations, got {len(clean)}")

    mu = np.mean(clean)
    sigma = np.std(clean, ddof=1)

    if sigma < 1e-15:
        return float("nan")

    if distribution == "normal":
        z = stats.norm.ppf(1 - confidence)
    elif distribution == "student_t":
        # / fit student-t to get degrees of freedom
        params = stats.t.fit(clean)
        df = params[0]
        z = stats.t.ppf(1 - confidence, df)
    else:
        raise ValueError(f"unknown distribution: {distribution}")

    var = -(mu + z * sigma)
    return float(var)


def var_historical(returns: np.ndarray, confidence: float = 0.95) -> float:
    # / empirical quantile VaR
    returns = np.asarray(returns, dtype=np.float64)
    clean = returns[~np.isnan(returns)]

    if len(clean) == 0:
        raise ValueError("returns must not be empty")

    quantile = np.percentile(clean, (1 - confidence) * 100)
    return float(-quantile)


def var_monte_carlo(
    returns: np.ndarray,
    confidence: float = 0.95,
    n_simulations: int = 10_000,
    rng: np.random.Generator | None = None,
) -> float:
    # / simulated VaR — resample from historical returns
    returns = np.asarray(returns, dtype=np.float64)
    clean = returns[~np.isnan(returns)]

    if len(clean) == 0:
        raise ValueError("returns must not be empty")

    rng = rng or np.random.default_rng()

    # / bootstrap: sample daily returns, sum to get period return
    sim_returns = rng.choice(clean, size=(n_simulations, len(clean)), replace=True)
    portfolio_returns = np.sum(sim_returns, axis=1)

    quantile = np.percentile(portfolio_returns, (1 - confidence) * 100)
    return float(-quantile)


def expected_shortfall(returns: np.ndarray, confidence: float = 0.95) -> float:
    # / CVaR = E[X | X < VaR]. average loss beyond VaR
    # / better for fat tails than VaR alone
    returns = np.asarray(returns, dtype=np.float64)
    clean = returns[~np.isnan(returns)]

    if len(clean) == 0:
        raise ValueError("returns must not be empty")

    threshold = np.percentile(clean, (1 - confidence) * 100)
    tail = clean[clean <= threshold]

    if len(tail) == 0:
        # / no observations beyond VaR — fall back to VaR
        return float(-threshold)

    return float(-np.mean(tail))


def max_drawdown(equity_curve: np.ndarray) -> tuple[float, float]:
    # / peak-to-trough decline from equity curve
    # / returns (max_drawdown_absolute, max_drawdown_pct)
    equity_curve = np.asarray(equity_curve, dtype=np.float64)
    clean = equity_curve[~np.isnan(equity_curve)]

    if len(clean) == 0:
        raise ValueError("equity_curve must not be empty")
    if len(clean) == 1:
        return 0.0, 0.0

    peak = clean[0]
    max_dd = 0.0
    max_dd_pct = 0.0

    for val in clean:
        if val > peak:
            peak = val
        dd = peak - val
        dd_pct = dd / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    return float(max_dd), float(max_dd_pct)


def evt_tail_estimation(
    returns: np.ndarray,
    threshold_quantile: float = 0.95,
) -> dict:
    # / fit Generalized Pareto Distribution to tail exceedances
    # / returns shape (xi), scale (sigma), extreme quantile estimates
    returns = np.asarray(returns, dtype=np.float64)
    clean = returns[~np.isnan(returns)]

    if len(clean) == 0:
        raise ValueError("returns must not be empty")

    # / work with losses (negative returns)
    losses = -clean
    threshold = np.percentile(losses, threshold_quantile * 100)
    exceedances = losses[losses > threshold] - threshold

    if len(exceedances) < 5:
        raise ValueError(
            f"need at least 5 exceedances for GPD fit, got {len(exceedances)}"
        )

    # / fit GPD to exceedances
    try:
        shape, loc, scale = stats.genpareto.fit(exceedances, floc=0)
    except Exception as e:
        logger.warning("gpd_fit_failed", error=str(e))
        return {
            "shape": float("nan"),
            "scale": float("nan"),
            "threshold": float(threshold),
            "n_exceedances": len(exceedances),
            "quantiles": {},
        }

    # / estimate extreme quantiles
    n = len(clean)
    n_u = len(exceedances)
    quantiles = {}

    for q in [0.99, 0.995, 0.999]:
        if shape != 0:
            xq = threshold + (scale / shape) * (
                (n / n_u * (1 - q)) ** (-shape) - 1
            )
        else:
            xq = threshold + scale * math.log(n / n_u * (1 - q))
        quantiles[q] = float(xq)

    return {
        "shape": float(shape),
        "scale": float(scale),
        "threshold": float(threshold),
        "n_exceedances": len(exceedances),
        "quantiles": quantiles,
    }


def risk_summary(
    returns: np.ndarray,
    equity_curve: np.ndarray | None = None,
    confidence: float = 0.95,
) -> dict:
    # / one-call comprehensive risk report
    returns = np.asarray(returns, dtype=np.float64)
    clean = returns[~np.isnan(returns)]

    if len(clean) == 0:
        raise ValueError("returns must not be empty")

    result = {
        "var_parametric": var_parametric(clean, confidence) if len(clean) >= 2 else float("nan"),
        "var_historical": var_historical(clean, confidence),
        "expected_shortfall": expected_shortfall(clean, confidence),
    }

    # / sharpe and sortino
    avg = float(np.mean(clean))
    std = float(np.std(clean, ddof=1)) if len(clean) > 1 else 0.0
    result["sharpe"] = (avg / std * math.sqrt(252)) if std > 0 else 0.0

    downside = clean[clean < 0]
    ds_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0
    if ds_std > 0:
        result["sortino"] = avg / ds_std * math.sqrt(252)
    elif avg > 0:
        result["sortino"] = float("inf")
    else:
        result["sortino"] = 0.0

    # / drawdown
    if equity_curve is not None:
        dd_abs, dd_pct = max_drawdown(equity_curve)
        result["max_drawdown"] = dd_abs
        result["max_drawdown_pct"] = dd_pct
    else:
        # / reconstruct equity curve from returns
        eq = np.cumprod(1 + clean) * 10000
        dd_abs, dd_pct = max_drawdown(eq)
        result["max_drawdown"] = dd_abs
        result["max_drawdown_pct"] = dd_pct

    # / EVT if enough data
    try:
        result["evt"] = evt_tail_estimation(clean)
    except ValueError:
        result["evt"] = None

    return result
