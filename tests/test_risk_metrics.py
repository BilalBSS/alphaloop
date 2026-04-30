# / tests for risk metrics module

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from src.quant.risk_metrics import (
    evt_tail_estimation,
    expected_shortfall,
    max_drawdown,
    risk_summary,
    var_historical,
    var_monte_carlo,
    var_parametric,
)

# ---------------------------------------------------------------------------
# / var_parametric
# ---------------------------------------------------------------------------

def test_var_parametric_normal_matches_analytical():
    # / analytical: VaR = -(mu + z * sigma), verify against known formula
    rng = np.random.default_rng(42)
    returns = rng.normal(0.001, 0.02, 1000)
    mu = np.mean(returns)
    sigma = np.std(returns, ddof=1)
    z = stats.norm.ppf(0.05)
    expected = -(mu + z * sigma)
    result = var_parametric(returns, confidence=0.95, distribution="normal")
    assert abs(result - expected) < 1e-10


def test_var_parametric_student_t():
    # / student-t should return a finite positive VaR for fat-tailed data
    rng = np.random.default_rng(42)
    returns = rng.standard_t(df=4, size=500) * 0.01
    result = var_parametric(returns, confidence=0.95, distribution="student_t")
    assert isinstance(result, float)
    assert result > 0
    assert math.isfinite(result)


def test_var_parametric_raises_for_empty():
    with pytest.raises(ValueError, match="empty"):
        var_parametric(np.array([]))


def test_var_parametric_raises_for_insufficient_data():
    with pytest.raises(ValueError, match="at least 2"):
        var_parametric(np.array([0.01]))


def test_var_parametric_returns_nan_for_zero_variance():
    # / all identical returns => zero variance => nan
    result = var_parametric(np.array([0.01, 0.01, 0.01, 0.01, 0.01]))
    assert math.isnan(result)


# ---------------------------------------------------------------------------
# / var_historical
# ---------------------------------------------------------------------------

def test_var_historical_basic():
    # / simple known distribution
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0, 0.02, 5000)
    result = var_historical(returns, confidence=0.95)
    assert isinstance(result, float)
    assert result > 0


def test_var_historical_matches_percentile():
    # / VaR should equal negative of the 5th percentile for 95% confidence
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0, 0.02, 1000)
    expected = -np.percentile(returns, 5)
    result = var_historical(returns, confidence=0.95)
    assert abs(result - expected) < 1e-10


# ---------------------------------------------------------------------------
# / var_monte_carlo
# ---------------------------------------------------------------------------

def test_var_monte_carlo_produces_reasonable_estimate():
    # / MC VaR should be positive and in a sensible range
    rng = np.random.default_rng(42)
    returns = rng.normal(0.001, 0.02, 252)
    result = var_monte_carlo(returns, confidence=0.95, n_simulations=10_000, rng=np.random.default_rng(99))
    assert isinstance(result, float)
    assert result > 0
    assert math.isfinite(result)


# ---------------------------------------------------------------------------
# / expected_shortfall (CVaR)
# ---------------------------------------------------------------------------

def test_expected_shortfall_gte_var():
    # / CVaR >= VaR is a mathematical invariant
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0, 0.02, 2000)
    var = var_historical(returns, confidence=0.95)
    cvar = expected_shortfall(returns, confidence=0.95)
    assert cvar >= var - 1e-12


def test_expected_shortfall_raises_for_empty():
    with pytest.raises(ValueError, match="empty"):
        expected_shortfall(np.array([]))


def test_expected_shortfall_known_answer():
    # / for uniform [-1, 0], 95% VaR threshold = 5th percentile = -0.95
    # / tail = values <= -0.95, mean of uniform[-1, -0.95] = -0.975
    # / CVaR = -(-0.975) = 0.975
    returns = np.linspace(-1.0, 0.0, 10001)
    result = expected_shortfall(returns, confidence=0.95)
    assert abs(result - 0.975) < 0.01


# ---------------------------------------------------------------------------
# / max_drawdown
# ---------------------------------------------------------------------------

def test_max_drawdown_known_equity_curve():
    # / peak at 100, trough at 70, then partial recovery
    equity = np.array([80.0, 100.0, 90.0, 70.0, 85.0])
    dd_abs, dd_pct = max_drawdown(equity)
    assert abs(dd_abs - 30.0) < 1e-10
    assert abs(dd_pct - 0.30) < 1e-10


def test_max_drawdown_monotonically_increasing():
    # / no drawdown on a monotonically rising curve
    equity = np.array([100.0, 110.0, 120.0, 130.0, 140.0])
    dd_abs, dd_pct = max_drawdown(equity)
    assert dd_abs == 0.0
    assert dd_pct == 0.0


def test_max_drawdown_single_element():
    dd_abs, dd_pct = max_drawdown(np.array([100.0]))
    assert dd_abs == 0.0
    assert dd_pct == 0.0


def test_max_drawdown_raises_for_empty():
    with pytest.raises(ValueError, match="empty"):
        max_drawdown(np.array([]))


# ---------------------------------------------------------------------------
# / evt_tail_estimation
# ---------------------------------------------------------------------------

def test_evt_tail_estimation_returns_correct_keys():
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0, 0.02, 2000)
    result = evt_tail_estimation(returns, threshold_quantile=0.90)
    expected_keys = {"shape", "scale", "threshold", "n_exceedances", "quantiles"}
    assert set(result.keys()) == expected_keys
    # / quantiles should contain 0.99, 0.995, 0.999
    assert set(result["quantiles"].keys()) == {0.99, 0.995, 0.999}


def test_evt_tail_estimation_raises_for_insufficient_exceedances():
    # / 10 observations with 95% threshold => ~0.5 exceedances, too few
    returns = np.array([0.01, 0.02, -0.01, 0.005, -0.005, 0.003, -0.002, 0.01, -0.01, 0.0])
    with pytest.raises(ValueError, match="at least 5 exceedances"):
        evt_tail_estimation(returns, threshold_quantile=0.95)


def test_evt_tail_estimation_raises_for_empty():
    with pytest.raises(ValueError, match="empty"):
        evt_tail_estimation(np.array([]))


# ---------------------------------------------------------------------------
# / risk_summary
# ---------------------------------------------------------------------------

def test_risk_summary_returns_all_expected_keys():
    rng = np.random.default_rng(42)
    returns = rng.normal(0.001, 0.02, 500)
    result = risk_summary(returns, confidence=0.95)
    required = {
        "var_parametric", "var_historical", "expected_shortfall",
        "sharpe", "sortino", "max_drawdown", "max_drawdown_pct", "evt",
    }
    assert required.issubset(set(result.keys()))


def test_risk_summary_with_equity_curve():
    rng = np.random.default_rng(42)
    returns = rng.normal(0.001, 0.02, 252)
    equity = np.cumprod(1 + returns) * 10000
    result = risk_summary(returns, equity_curve=equity, confidence=0.95)
    assert result["max_drawdown"] >= 0
    assert 0 <= result["max_drawdown_pct"] <= 1.0


def test_risk_summary_without_equity_curve():
    # / equity curve reconstructed from returns internally
    rng = np.random.default_rng(42)
    returns = rng.normal(0.001, 0.02, 252)
    result = risk_summary(returns, equity_curve=None, confidence=0.95)
    assert result["max_drawdown"] >= 0
    assert 0 <= result["max_drawdown_pct"] <= 1.0


# ---------------------------------------------------------------------------
# / CVaR >= VaR invariant across confidence levels
# ---------------------------------------------------------------------------

def test_cvar_gte_var_invariant_multiple_confidence_levels():
    # / must hold at 90%, 95%, 99%
    rng = np.random.default_rng(42)
    returns = rng.standard_t(df=5, size=5000) * 0.01
    for conf in [0.90, 0.95, 0.99]:
        var = var_historical(returns, confidence=conf)
        cvar = expected_shortfall(returns, confidence=conf)
        assert cvar >= var - 1e-12, f"CVaR < VaR at confidence={conf}"


# ---------------------------------------------------------------------------
# / additional deep tests
# ---------------------------------------------------------------------------

def test_var_parametric_exact_normal():
    # / for N(0, 0.02), 95% VaR = -(0 + z_0.05 * 0.02) = 1.645 * 0.02 = 0.0329
    n = 100_000
    rng = np.random.default_rng(42)
    returns = rng.normal(0, 0.02, n)
    mu = np.mean(returns)
    sigma = np.std(returns, ddof=1)
    # / analytical parametric VaR
    expected = -(mu + stats.norm.ppf(0.05) * sigma)
    result = var_parametric(returns, confidence=0.95, distribution="normal")
    assert abs(result - expected) < 1e-10
    # / with large n, mu~0 and sigma~0.02, so VaR ~ 1.645*0.02 = 0.0329
    assert abs(result - 1.645 * 0.02) < 0.001


def test_var_historical_exact_percentile():
    # / construct known returns where exact quantile is deterministic
    returns = np.array([-0.05, -0.04, -0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04])
    # / 95% confidence -> 5th percentile
    expected = -np.percentile(returns, 5)
    result = var_historical(returns, confidence=0.95)
    assert abs(result - expected) < 1e-10


def test_cvar_greater_than_var():
    # / invariant: CVaR >= VaR for any return distribution
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0, 0.03, 5000)
    var = var_historical(returns, confidence=0.95)
    cvar = expected_shortfall(returns, confidence=0.95)
    assert cvar >= var - 1e-12


def test_max_drawdown_monotonic_increase_is_zero():
    # / strictly increasing equity curve has zero drawdown
    equity = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    dd_abs, dd_pct = max_drawdown(equity)
    assert dd_abs == 0.0
    assert dd_pct == 0.0


def test_max_drawdown_exact_calculation():
    # / [100, 120, 90, 130, 80] -> peak=130 at index 3, trough=80 at index 4
    # / max drawdown absolute = 130 - 80 = 50, pct = 50/130
    equity = np.array([100.0, 120.0, 90.0, 130.0, 80.0])
    dd_abs, dd_pct = max_drawdown(equity)
    assert abs(dd_abs - 50.0) < 1e-10
    assert abs(dd_pct - 50.0 / 130.0) < 1e-10
    assert abs(dd_pct - 0.384615384615) < 1e-6


def test_evt_with_normal_tail():
    # / GPD shape parameter should be near 0 for normal distribution tails
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0, 0.02, 10_000)
    result = evt_tail_estimation(returns, threshold_quantile=0.90)
    # / for normal tails, GPD shape (xi) should be near 0
    assert abs(result["shape"]) < 0.3


def test_risk_summary_contains_all_keys():
    # / verify every expected key is present in summary
    rng = np.random.default_rng(42)
    returns = rng.normal(0.001, 0.02, 500)
    result = risk_summary(returns, confidence=0.95)
    expected_keys = {
        "var_parametric", "var_historical", "expected_shortfall",
        "sharpe", "sortino", "max_drawdown", "max_drawdown_pct", "evt",
    }
    for key in expected_keys:
        assert key in result, f"missing key: {key}"


def test_sharpe_annualized_correctly():
    # / sharpe = (mean / std) * sqrt(252) from the source code
    rng = np.random.default_rng(42)
    returns = rng.normal(0.001, 0.02, 500)
    result = risk_summary(returns, confidence=0.95)
    # / recompute manually
    clean = returns[~np.isnan(returns)]
    avg = float(np.mean(clean))
    std = float(np.std(clean, ddof=1))
    expected_sharpe = (avg / std) * math.sqrt(252) if std > 0 else 0.0
    assert abs(result["sharpe"] - expected_sharpe) < 1e-10


def test_sortino_only_uses_downside_std():
    # / sortino = (mean / downside_std) * sqrt(252)
    rng = np.random.default_rng(42)
    returns = rng.normal(0.001, 0.02, 1000)
    result = risk_summary(returns, confidence=0.95)
    clean = returns[~np.isnan(returns)]
    avg = float(np.mean(clean))
    downside = clean[clean < 0]
    ds_std = float(np.std(downside, ddof=1))
    expected_sortino = avg / ds_std * math.sqrt(252) if ds_std > 0 else 0.0
    assert abs(result["sortino"] - expected_sortino) < 1e-10


def test_empty_returns_raises():
    # / all functions should raise ValueError on empty input
    with pytest.raises(ValueError, match="empty"):
        var_parametric(np.array([]))
    with pytest.raises(ValueError, match="empty"):
        var_historical(np.array([]))
    with pytest.raises(ValueError, match="empty"):
        expected_shortfall(np.array([]))
    with pytest.raises(ValueError, match="empty"):
        max_drawdown(np.array([]))
    with pytest.raises(ValueError, match="empty"):
        risk_summary(np.array([]))
