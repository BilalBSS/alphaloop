# / importance sampling for tail risk estimation
# / exponential tilting shifts sampling toward rare events
# / 100-10,000x variance reduction on extreme tail probabilities

from __future__ import annotations

import numpy as np
from scipy import stats
import structlog

logger = structlog.get_logger(__name__)


def exponential_tilt(
    rng: np.random.Generator | None,
    n: int,
    original_dist: stats.rv_frozen,
    tilt_param: float,
) -> tuple[np.ndarray, np.ndarray]:
    # / shifts sampling distribution toward the tail via exponential tilting
    # / returns (samples, likelihood_ratios)
    # / likelihood ratio L(x) = f_original(x) / f_tilted(x)
    if n <= 0:
        raise ValueError("n must be positive")
    rng = rng or np.random.default_rng()

    # / for normal distributions: tilted distribution is N(mu + gamma*sigma^2, sigma^2)
    # / extract mean and std from the frozen distribution
    mu = original_dist.mean()
    sigma = original_dist.std()

    if sigma <= 0:
        raise ValueError("distribution must have positive standard deviation")

    # / tilted mean
    tilted_mu = mu + tilt_param * sigma**2
    tilted_dist = stats.norm(loc=tilted_mu, scale=sigma)

    # / sample from tilted distribution
    samples = tilted_dist.rvs(size=n, random_state=rng)

    # / likelihood ratios: f_original / f_tilted
    log_original = original_dist.logpdf(samples)
    log_tilted = tilted_dist.logpdf(samples)
    log_ratios = log_original - log_tilted

    # / clamp extreme log ratios to prevent overflow
    log_ratios = np.clip(log_ratios, -50, 50)
    likelihood_ratios = np.exp(log_ratios)

    return samples, likelihood_ratios


def estimate_tail_probability(
    samples: np.ndarray,
    weights: np.ndarray,
    threshold: float,
) -> dict:
    # / importance-weighted tail probability estimate P(X > threshold)
    # / returns dict with probability, ci_lower, ci_upper, ess
    samples = np.asarray(samples, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)

    if len(samples) == 0:
        raise ValueError("samples must not be empty")
    if len(samples) != len(weights):
        raise ValueError("samples and weights must have same length")

    # / filter nans
    mask = ~(np.isnan(samples) | np.isnan(weights))
    samples = samples[mask]
    weights = weights[mask]

    if len(samples) == 0:
        return {"probability": float("nan"), "ci_lower": float("nan"),
                "ci_upper": float("nan"), "ess": 0.0}

    # / normalize weights
    w_sum = np.sum(weights)
    if w_sum <= 0:
        return {"probability": float("nan"), "ci_lower": float("nan"),
                "ci_upper": float("nan"), "ess": 0.0}

    w_norm = weights / w_sum

    # / weighted indicator
    indicators = (samples > threshold).astype(float)
    prob = float(np.sum(w_norm * indicators))

    # / effective sample size
    ess = effective_sample_size(weights)

    # / confidence interval using CLT with effective sample size
    if ess > 1:
        se = np.sqrt(prob * (1 - prob) / ess)
        ci_lower = max(0.0, prob - 1.96 * se)
        ci_upper = min(1.0, prob + 1.96 * se)
    else:
        ci_lower = 0.0
        ci_upper = 1.0

    return {
        "probability": prob,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "ess": ess,
    }


def optimal_tilt_parameter(
    target_threshold: float,
    mu: float,
    sigma: float,
) -> float:
    # / chooses tilt gamma so the threshold is ~1 std dev from tilted mean
    # / tilted_mu = mu + gamma * sigma^2
    # / we want: target_threshold = tilted_mu + sigma (1 std dev away)
    # / => gamma = (target_threshold - mu - sigma) / sigma^2
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    gamma = (target_threshold - mu - sigma) / (sigma**2)
    return float(gamma)


def effective_sample_size(weights: np.ndarray) -> float:
    # / ESS = (sum w)^2 / sum(w^2)
    # / measures weight degeneracy. ESS=n means uniform, ESS=1 means degenerate
    weights = np.asarray(weights, dtype=np.float64)
    if len(weights) == 0:
        raise ValueError("weights must not be empty")

    mask = ~np.isnan(weights)
    w = weights[mask]
    if len(w) == 0:
        return 0.0

    w_sum = np.sum(w)
    w_sq_sum = np.sum(w**2)
    if w_sq_sum <= 0:
        return 0.0

    return float(w_sum**2 / w_sq_sum)
