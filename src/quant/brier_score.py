# / strategy calibration tracking
# / BS = mean((predicted - actual)^2)
# / < 0.20 = good, < 0.10 = excellent
# / Murphy decomposition: BS = reliability - resolution + uncertainty

from __future__ import annotations

import numpy as np


def brier_score(predictions: np.ndarray, outcomes: np.ndarray) -> float:
    # / scalar brier score
    predictions = np.asarray(predictions, dtype=np.float64)
    outcomes = np.asarray(outcomes, dtype=np.float64)

    if len(predictions) == 0 or len(outcomes) == 0:
        raise ValueError("inputs must not be empty")
    if len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must have same length")

    # / handle all-nan
    mask = ~(np.isnan(predictions) | np.isnan(outcomes))
    p = predictions[mask]
    o = outcomes[mask]

    if len(p) == 0:
        return float("nan")

    return float(np.mean((p - o) ** 2))


def calibration_curve(
    predictions: np.ndarray,
    outcomes: np.ndarray,
    n_bins: int = 10,
) -> dict:
    # / binned predicted vs actual for reliability diagram
    # / returns dict with bin_centers, bin_freqs, bin_counts
    predictions = np.asarray(predictions, dtype=np.float64)
    outcomes = np.asarray(outcomes, dtype=np.float64)

    if len(predictions) == 0 or len(outcomes) == 0:
        raise ValueError("inputs must not be empty")
    if len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must have same length")
    if n_bins <= 0:
        raise ValueError("n_bins must be positive")

    mask = ~(np.isnan(predictions) | np.isnan(outcomes))
    p = predictions[mask]
    o = outcomes[mask]

    if len(p) == 0:
        return {"bin_centers": [], "bin_freqs": [], "bin_counts": []}

    bin_edges = np.linspace(0, 1, n_bins + 1)
    bin_centers = []
    bin_freqs = []
    bin_counts = []

    for i in range(n_bins):
        lo = bin_edges[i]
        hi = bin_edges[i + 1]
        if i == n_bins - 1:
            mask_bin = (p >= lo) & (p <= hi)
        else:
            mask_bin = (p >= lo) & (p < hi)

        count = int(np.sum(mask_bin))
        bin_counts.append(count)
        bin_centers.append(float((lo + hi) / 2))

        if count > 0:
            bin_freqs.append(float(np.mean(o[mask_bin])))
        else:
            bin_freqs.append(float("nan"))

    return {
        "bin_centers": bin_centers,
        "bin_freqs": bin_freqs,
        "bin_counts": bin_counts,
    }


def resolution_reliability_uncertainty(
    predictions: np.ndarray,
    outcomes: np.ndarray,
    n_bins: int = 10,
) -> dict:
    # / murphy decomposition: BS = reliability - resolution + uncertainty
    # / reliability: how close binned predictions are to binned outcomes (lower = better)
    # / resolution: how much binned outcomes differ from base rate (higher = better)
    # / uncertainty: base rate entropy (fixed for a dataset)
    predictions = np.asarray(predictions, dtype=np.float64)
    outcomes = np.asarray(outcomes, dtype=np.float64)

    if len(predictions) == 0 or len(outcomes) == 0:
        raise ValueError("inputs must not be empty")
    if len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must have same length")

    mask = ~(np.isnan(predictions) | np.isnan(outcomes))
    p = predictions[mask]
    o = outcomes[mask]

    if len(p) == 0:
        return {
            "reliability": float("nan"),
            "resolution": float("nan"),
            "uncertainty": float("nan"),
            "brier_score": float("nan"),
        }

    n_total = len(p)
    base_rate = np.mean(o)
    uncertainty = base_rate * (1 - base_rate)

    bin_edges = np.linspace(0, 1, n_bins + 1)
    reliability = 0.0
    resolution = 0.0

    for i in range(n_bins):
        lo = bin_edges[i]
        hi = bin_edges[i + 1]
        if i == n_bins - 1:
            mask_bin = (p >= lo) & (p <= hi)
        else:
            mask_bin = (p >= lo) & (p < hi)

        n_k = np.sum(mask_bin)
        if n_k == 0:
            continue

        avg_pred = np.mean(p[mask_bin])
        avg_obs = np.mean(o[mask_bin])

        reliability += n_k * (avg_pred - avg_obs) ** 2
        resolution += n_k * (avg_obs - base_rate) ** 2

    reliability /= n_total
    resolution /= n_total

    bs = float(np.mean((p - o) ** 2))

    return {
        "reliability": float(reliability),
        "resolution": float(resolution),
        "uncertainty": float(uncertainty),
        "brier_score": bs,
    }


def rolling_brier(
    predictions: np.ndarray,
    outcomes: np.ndarray,
    window: int = 50,
) -> np.ndarray:
    # / windowed brier score for tracking drift over time
    # / returns array of rolling brier scores (nan-padded at start)
    predictions = np.asarray(predictions, dtype=np.float64)
    outcomes = np.asarray(outcomes, dtype=np.float64)

    if len(predictions) == 0 or len(outcomes) == 0:
        raise ValueError("inputs must not be empty")
    if len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must have same length")
    if window <= 0:
        raise ValueError("window must be positive")

    n = len(predictions)
    result = np.full(n, float("nan"))

    for i in range(window - 1, n):
        start = i - window + 1
        p_win = predictions[start : i + 1]
        o_win = outcomes[start : i + 1]

        mask = ~(np.isnan(p_win) | np.isnan(o_win))
        if np.sum(mask) > 0:
            result[i] = float(np.mean((p_win[mask] - o_win[mask]) ** 2))

    return result
