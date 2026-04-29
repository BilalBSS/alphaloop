
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np


def antithetic_sample(
    rng: np.random.Generator | None,
    n: int,
    dim: int = 1,
) -> np.ndarray:
    if n <= 0:
        raise ValueError("n must be positive")
    rng = rng or np.random.default_rng()
    z = rng.standard_normal((n, dim))
    return np.vstack([z, -z])


def stratified_sample(
    rng: np.random.Generator | None,
    n: int,
    strata: int = 10,
) -> np.ndarray:
    if n <= 0:
        raise ValueError("n must be positive")
    if strata <= 0:
        raise ValueError("strata must be positive")
    rng = rng or np.random.default_rng()

    from scipy.stats import norm

    samples_per = n // strata
    remainder = n % strata

    all_samples = []
    for i in range(strata):
        k = samples_per + (1 if i < remainder else 0)
        if k == 0:
            continue
        lo = i / strata
        hi = (i + 1) / strata
        u = rng.uniform(lo, hi, size=k)
        all_samples.append(u)

    u_all = np.concatenate(all_samples)
    u_all = np.clip(u_all, 1e-10, 1 - 1e-10)
    return np.asarray(norm.ppf(u_all))


def control_variate_adjust(
    mc_estimates: np.ndarray,
    control_estimates: np.ndarray,
    control_exact: float,
) -> tuple[float, float]:
    # / applies control variate correction
    # / returns (adjusted_mean, variance_reduction_ratio)
    if len(mc_estimates) == 0 or len(control_estimates) == 0:
        raise ValueError("input arrays must not be empty")
    if len(mc_estimates) != len(control_estimates):
        raise ValueError("mc_estimates and control_estimates must have same length")

    mc = np.asarray(mc_estimates, dtype=np.float64)
    ctrl = np.asarray(control_estimates, dtype=np.float64)

    # / strip nans
    mask = ~(np.isnan(mc) | np.isnan(ctrl))
    mc = mc[mask]
    ctrl = ctrl[mask]

    if len(mc) < 2:
        return float(np.nanmean(mc_estimates)), 1.0

    ctrl_var = np.var(ctrl, ddof=1)
    if ctrl_var < 1e-15:
        return float(np.mean(mc)), 1.0

    cov_mc_ctrl = np.cov(mc, ctrl, ddof=1)[0, 1]
    c = -cov_mc_ctrl / ctrl_var

    # / adjusted estimates
    adjusted = mc + c * (ctrl - control_exact)
    adjusted_mean = float(np.mean(adjusted))

    # / variance reduction ratio
    crude_var = np.var(mc, ddof=1)
    adjusted_var = np.var(adjusted, ddof=1)
    vr_ratio = crude_var / adjusted_var if adjusted_var > 1e-15 else float("inf")

    return adjusted_mean, float(vr_ratio)


def variance_reduction_ratio(crude_var: float, reduced_var: float) -> float:
    # / measures improvement factor
    if reduced_var <= 0:
        return float("inf")
    return crude_var / reduced_var


def run_simulation(
    func: Callable[[np.ndarray], np.ndarray],
    n_samples: int = 10_000,
    variance_reduction: str = "antithetic",
    rng: np.random.Generator | None = None,
    dim: int = 1,
) -> dict[str, Any]:
    if n_samples <= 0:
        raise ValueError("n_samples must be positive")
    rng = rng or np.random.default_rng()

    if variance_reduction == "antithetic":
        samples = antithetic_sample(rng, n_samples // 2, dim)
    elif variance_reduction == "stratified":
        samples = stratified_sample(rng, n_samples).reshape(-1, dim)
    elif variance_reduction == "none":
        samples = rng.standard_normal((n_samples, dim))
    else:
        raise ValueError(f"unknown variance_reduction method: {variance_reduction}")

    estimates = func(samples)
    estimates = np.asarray(estimates, dtype=np.float64)

    # / drop nans
    clean = estimates[~np.isnan(estimates)]
    if len(clean) == 0:
        return {
            "mean": float("nan"),
            "std": float("nan"),
            "ci_lower": float("nan"),
            "ci_upper": float("nan"),
            "n_effective": 0,
            "vr_method": variance_reduction,
        }

    mean = float(np.mean(clean))
    std = float(np.std(clean, ddof=1)) if len(clean) > 1 else 0.0
    se = std / np.sqrt(len(clean)) if len(clean) > 0 else 0.0

    return {
        "mean": mean,
        "std": std,
        "ci_lower": mean - 1.96 * se,
        "ci_upper": mean + 1.96 * se,
        "n_effective": len(clean),
        "vr_method": variance_reduction,
    }
