# / tests for monte carlo variance reduction module

from __future__ import annotations

import numpy as np
import pytest

from src.quant.monte_carlo import (
    antithetic_sample,
    control_variate_adjust,
    run_simulation,
    stratified_sample,
    variance_reduction_ratio,
)

# --- antithetic_sample ---


def test_antithetic_sample_correct_shape():
    # / should return (2*n, dim) array
    rng = np.random.default_rng(42)
    result = antithetic_sample(rng, n=100, dim=3)
    assert result.shape == (200, 3)


def test_antithetic_sample_z_and_neg_z():
    # / first half and second half should be exact negatives
    rng = np.random.default_rng(42)
    result = antithetic_sample(rng, n=50, dim=2)
    z = result[:50]
    neg_z = result[50:]
    np.testing.assert_array_almost_equal(z, -neg_z)


def test_antithetic_sample_different_dims():
    # / works for dim=1, dim=5, dim=10
    rng = np.random.default_rng(42)
    for dim in [1, 5, 10]:
        result = antithetic_sample(rng, n=20, dim=dim)
        assert result.shape == (40, dim)


def test_antithetic_sample_raises_on_non_positive_n():
    # / n <= 0 should raise ValueError
    rng = np.random.default_rng(42)
    with pytest.raises(ValueError, match="n must be positive"):
        antithetic_sample(rng, n=0)
    with pytest.raises(ValueError, match="n must be positive"):
        antithetic_sample(rng, n=-5)


# --- stratified_sample ---


def test_stratified_sample_correct_length():
    # / should return exactly n samples
    rng = np.random.default_rng(42)
    result = stratified_sample(rng, n=100, strata=10)
    assert len(result) == 100


def test_stratified_sample_covers_full_range():
    # / stratified samples should span the normal range broadly
    rng = np.random.default_rng(42)
    result = stratified_sample(rng, n=1000, strata=20)
    # / with 1000 stratified samples we expect coverage of tails
    assert result.min() < -2.0
    assert result.max() > 2.0


def test_stratified_sample_remainder_allocation():
    # / n=17, strata=5 -> 3 strata get 4 samples, 2 strata get 3 samples
    rng = np.random.default_rng(42)
    result = stratified_sample(rng, n=17, strata=5)
    assert len(result) == 17


def test_stratified_sample_raises_on_invalid_inputs():
    # / n<=0 or strata<=0 should raise ValueError
    rng = np.random.default_rng(42)
    with pytest.raises(ValueError, match="n must be positive"):
        stratified_sample(rng, n=0, strata=10)
    with pytest.raises(ValueError, match="strata must be positive"):
        stratified_sample(rng, n=100, strata=0)


# --- control_variate_adjust ---


def test_control_variate_adjust_reduces_variance():
    # / control variate should reduce variance on a known correlated problem
    rng = np.random.default_rng(42)
    n = 5000
    z = rng.standard_normal(n)
    # / mc estimates: e^Z (want E[e^Z] = e^0.5 ≈ 1.6487)
    mc_estimates = np.exp(z)
    # / control: Z itself (known mean = 0)
    control_estimates = z
    control_exact = 0.0

    mean, vr_ratio = control_variate_adjust(mc_estimates, control_estimates, control_exact)
    # / vr_ratio > 1 means variance was reduced
    assert vr_ratio > 1.0
    # / adjusted mean should be close to true value e^0.5
    assert abs(mean - np.exp(0.5)) < 0.1


def test_control_variate_adjust_returns_tuple():
    # / should return (mean, vr_ratio) as a 2-tuple
    rng = np.random.default_rng(42)
    mc = rng.standard_normal(100)
    ctrl = rng.standard_normal(100)
    result = control_variate_adjust(mc, ctrl, 0.0)
    assert isinstance(result, tuple)
    assert len(result) == 2
    mean, vr = result
    assert isinstance(mean, float)
    assert isinstance(vr, float)


def test_control_variate_adjust_raises_on_empty():
    # / empty arrays should raise ValueError
    with pytest.raises(ValueError, match="must not be empty"):
        control_variate_adjust(np.array([]), np.array([]), 0.0)


def test_control_variate_adjust_raises_on_mismatched_lengths():
    # / different-length arrays should raise ValueError
    with pytest.raises(ValueError, match="same length"):
        control_variate_adjust(np.array([1.0, 2.0]), np.array([1.0]), 0.0)


def test_control_variate_adjust_handles_zero_variance_control():
    # / when control has zero variance, should return raw mean with ratio 1.0
    mc = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    ctrl = np.array([1.0, 1.0, 1.0, 1.0, 1.0])
    mean, vr_ratio = control_variate_adjust(mc, ctrl, 1.0)
    assert mean == pytest.approx(np.mean(mc))
    assert vr_ratio == 1.0


# --- variance_reduction_ratio ---


def test_variance_reduction_ratio_basic():
    # / simple ratio calculation
    assert variance_reduction_ratio(10.0, 2.0) == pytest.approx(5.0)
    assert variance_reduction_ratio(100.0, 50.0) == pytest.approx(2.0)


def test_variance_reduction_ratio_zero_reduced():
    # / zero reduced_var should return inf
    result = variance_reduction_ratio(10.0, 0.0)
    assert result == float("inf")


# --- run_simulation ---


def test_run_simulation_antithetic():
    # / antithetic method returns valid result dict
    rng = np.random.default_rng(42)
    func = lambda samples: np.mean(samples, axis=1)
    result = run_simulation(func, n_samples=1000, variance_reduction="antithetic", rng=rng)

    assert "mean" in result
    assert "std" in result
    assert "ci_lower" in result
    assert "ci_upper" in result
    assert "n_effective" in result
    assert result["vr_method"] == "antithetic"
    assert result["n_effective"] == 1000
    assert result["ci_lower"] < result["mean"] < result["ci_upper"]


def test_run_simulation_stratified():
    # / stratified method returns valid result dict
    rng = np.random.default_rng(42)
    func = lambda samples: samples[:, 0]
    result = run_simulation(func, n_samples=500, variance_reduction="stratified", rng=rng, dim=1)

    assert result["vr_method"] == "stratified"
    assert result["n_effective"] == 500
    assert np.isfinite(result["mean"])
    assert np.isfinite(result["std"])


def test_run_simulation_antithetic_reduces_variance():
    # / antithetic should produce lower std than no VR on a monotone payoff
    rng_none = np.random.default_rng(42)
    rng_anti = np.random.default_rng(42)

    # / monotone payoff: e^Z benefits from antithetic
    func = lambda samples: np.exp(np.mean(samples, axis=1))

    result_none = run_simulation(func, n_samples=10000, variance_reduction="none", rng=rng_none)
    result_anti = run_simulation(func, n_samples=10000, variance_reduction="antithetic", rng=rng_anti)

    # / antithetic should have tighter confidence interval
    ci_width_none = result_none["ci_upper"] - result_none["ci_lower"]
    ci_width_anti = result_anti["ci_upper"] - result_anti["ci_lower"]
    assert ci_width_anti < ci_width_none


# --- additional deep tests ---


def test_antithetic_exact_negation():
    # / z[i] == -z[i+n] for each pair in antithetic sample
    rng = np.random.default_rng(123)
    n = 200
    dim = 4
    result = antithetic_sample(rng, n=n, dim=dim)
    first_half = result[:n]
    second_half = result[n:]
    np.testing.assert_array_almost_equal(first_half, -second_half)


def test_stratified_covers_all_strata():
    # / each stratum gets at least one sample when n >= strata
    rng = np.random.default_rng(42)
    from scipy.stats import norm
    n = 100
    strata = 10
    result = stratified_sample(rng, n=n, strata=strata)
    # / inverse-transform back to uniform to check strata coverage
    u = norm.cdf(result)
    for i in range(strata):
        lo = i / strata
        hi = (i + 1) / strata
        count = np.sum((u >= lo) & (u < hi))
        assert count >= 1, f"stratum [{lo}, {hi}) has no samples"


def test_stratified_with_n_less_than_strata():
    # / still works when n < strata, some strata will be empty
    rng = np.random.default_rng(42)
    n = 3
    strata = 10
    result = stratified_sample(rng, n=n, strata=strata)
    assert len(result) == n
    assert np.all(np.isfinite(result))


def test_control_variate_known_answer():
    # / construct a case where mc and control are perfectly correlated
    # / mc = 2*x + 1, control = x, control_exact = 0
    # / true mean of mc = 2*E[x] + 1 = 1 (since E[x]=0)
    rng = np.random.default_rng(42)
    n = 1000
    x = rng.standard_normal(n)
    mc_estimates = 2 * x + 1.0
    control_estimates = x.copy()
    control_exact = 0.0
    mean, vr_ratio = control_variate_adjust(mc_estimates, control_estimates, control_exact)
    # / adjusted mean should be very close to 1.0
    assert abs(mean - 1.0) < 0.05
    # / perfect correlation means huge variance reduction
    assert vr_ratio > 5.0


def test_control_variate_zero_ctrl_variance_returns_crude_mean():
    # / when control has zero variance, return crude mean with ratio 1.0
    mc = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
    ctrl = np.array([5.0, 5.0, 5.0, 5.0, 5.0])
    mean, vr_ratio = control_variate_adjust(mc, ctrl, 5.0)
    assert mean == pytest.approx(np.mean(mc))
    assert vr_ratio == 1.0


def test_run_simulation_none_method_uses_raw_normal():
    # / variance_reduction="none" uses raw standard normal samples
    rng = np.random.default_rng(42)
    func = lambda samples: samples[:, 0]
    result = run_simulation(func, n_samples=500, variance_reduction="none", rng=rng, dim=1)
    assert result["vr_method"] == "none"
    assert result["n_effective"] == 500
    assert np.isfinite(result["mean"])


def test_run_simulation_returns_ci_contains_mean():
    # / confidence interval must contain the mean
    rng = np.random.default_rng(42)
    func = lambda samples: samples[:, 0]
    result = run_simulation(func, n_samples=2000, variance_reduction="antithetic", rng=rng, dim=1)
    assert result["ci_lower"] <= result["mean"] <= result["ci_upper"]


def test_variance_reduction_ratio_less_than_1_means_no_improvement():
    # / ratio < 1 means reduced_var > crude_var (worse)
    ratio = variance_reduction_ratio(1.0, 5.0)
    assert ratio < 1.0
    assert ratio == pytest.approx(0.2)
