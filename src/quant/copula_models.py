# / copula models for tail dependence
# / gaussian (lambda=0 baseline), student-t (symmetric tail), clayton (lower tail/crash)
# / risk agent uses t-copula to gate trades: if tail dependence > 0.30, reject or size down

from __future__ import annotations

import numpy as np
from scipy import stats, optimize, special
import structlog

logger = structlog.get_logger(__name__)


def gaussian_copula_fit(u_data: np.ndarray) -> np.ndarray:
    # / fit gaussian copula. returns correlation matrix.
    # / tail dependence lambda = 0 — baseline only.
    u_data = np.asarray(u_data, dtype=np.float64)

    if u_data.ndim != 2:
        raise ValueError("u_data must be 2D (n_samples, n_vars)")
    if u_data.shape[0] < 3:
        raise ValueError(f"need at least 3 observations, got {u_data.shape[0]}")

    # / transform uniform margins to normal
    # / clip to avoid inf at 0 and 1
    u_clipped = np.clip(u_data, 1e-10, 1 - 1e-10)
    z = stats.norm.ppf(u_clipped)

    # / correlation matrix from transformed data
    corr = np.corrcoef(z, rowvar=False)

    return corr


def student_t_copula_fit(u_data: np.ndarray) -> tuple[float, np.ndarray]:
    # / fit student-t copula via profile likelihood
    # / fix nu, optimize sigma via Kendall's tau (nu-independent), then line-search over nu
    # / returns (nu, correlation_matrix)
    u_data = np.asarray(u_data, dtype=np.float64)

    if u_data.ndim != 2:
        raise ValueError("u_data must be 2D (n_samples, n_vars)")
    if u_data.shape[0] < 5:
        raise ValueError(f"need at least 5 observations, got {u_data.shape[0]}")

    n_samples, n_vars = u_data.shape
    u_clipped = np.clip(u_data, 1e-10, 1 - 1e-10)

    # / step 1: estimate correlation matrix via Kendall's tau (nu-independent)
    # / tau = (2/pi) * arcsin(rho) for t-copula => rho = sin(pi*tau/2)
    corr = np.eye(n_vars)
    for i in range(n_vars):
        for j in range(i + 1, n_vars):
            tau, _ = stats.kendalltau(u_data[:, i], u_data[:, j])
            if np.isnan(tau):
                tau = 0.0
            rho = np.sin(np.pi * tau / 2)
            # / clamp to valid range
            rho = np.clip(rho, -0.999, 0.999)
            corr[i, j] = rho
            corr[j, i] = rho

    # / ensure positive definiteness
    eigvals = np.linalg.eigvalsh(corr)
    if np.min(eigvals) < 1e-6:
        # / nearest positive definite matrix
        corr = _nearest_pd(corr)

    # / step 2: profile likelihood over nu using Brent's method
    # / transform uniform margins to t-quantiles for given nu
    def neg_log_likelihood(nu):
        if nu <= 2.0:
            return 1e10
        try:
            z = stats.t.ppf(u_clipped, df=nu)
            # / t-copula log-likelihood (simplified for bivariate+)
            n = z.shape[0]
            d = z.shape[1]

            # / log-density of multivariate t relative to product of marginal t
            det_corr = np.linalg.det(corr)
            if det_corr <= 0:
                return 1e10

            inv_corr = np.linalg.inv(corr)
            log_det = np.log(det_corr)

            ll = 0.0
            ll += n * special.gammaln((nu + d) / 2)
            ll -= n * d * special.gammaln((nu + 1) / 2)
            ll += n * (d - 1) * special.gammaln(nu / 2)
            ll -= 0.5 * n * log_det

            for k in range(n):
                quad = z[k] @ inv_corr @ z[k]
                ll -= ((nu + d) / 2) * np.log(1 + quad / nu)
                # / add back marginal t densities
                for j in range(d):
                    ll += ((nu + 1) / 2) * np.log(1 + z[k, j] ** 2 / nu)

            return -ll
        except Exception:
            return 1e10

    result = optimize.minimize_scalar(
        neg_log_likelihood,
        bounds=(2.5, 30),
        method="bounded",
        options={"xatol": 0.1},
    )

    nu = result.x if result.success else 5.0

    if not result.success:
        logger.warning("t_copula_nu_optimization_failed", falling_back_to=nu)

    return float(nu), corr


def clayton_copula_fit(u_data: np.ndarray) -> float:
    # / fit clayton copula (bivariate only)
    # / returns theta via Kendall's tau inversion: theta = 2*tau / (1 - tau)
    # / lower tail dependence lambda_L = 2^(-1/theta)
    u_data = np.asarray(u_data, dtype=np.float64)

    if u_data.ndim != 2 or u_data.shape[1] != 2:
        raise ValueError("clayton copula requires bivariate data (n_samples, 2)")
    if u_data.shape[0] < 3:
        raise ValueError(f"need at least 3 observations, got {u_data.shape[0]}")

    tau, _ = stats.kendalltau(u_data[:, 0], u_data[:, 1])

    if np.isnan(tau):
        raise ValueError("cannot compute Kendall's tau — degenerate data")

    if tau <= 0:
        # / clayton requires positive dependence
        logger.warning("clayton_negative_tau", tau=tau)
        return 0.01  # / minimal positive dependence

    if tau >= 1.0:
        tau = 0.999

    theta = 2 * tau / (1 - tau)
    return float(theta)


def tail_dependence_coefficient(
    copula_type: str,
    params: float | tuple,
) -> dict:
    # / compute tail dependence lambda for given copula
    # / returns dict with lambda_lower, lambda_upper
    if copula_type == "gaussian":
        # / gaussian copula: lambda = 0 always (asymptotically independent)
        return {"lambda_lower": 0.0, "lambda_upper": 0.0}

    elif copula_type == "student_t":
        # / symmetric: lambda = 2 * t_{nu+1}(-sqrt((nu+1)(1-rho)/(1+rho)))
        if isinstance(params, tuple) and len(params) >= 2:
            nu, corr = params[0], params[1]
        else:
            raise ValueError("student_t params must be (nu, correlation_matrix)")

        # / extract rho for bivariate, or average for multivariate
        if isinstance(corr, np.ndarray):
            if corr.ndim == 2:
                # / use off-diagonal average
                mask = ~np.eye(corr.shape[0], dtype=bool)
                rho = float(np.mean(corr[mask]))
            else:
                rho = float(corr)
        else:
            rho = float(corr)

        rho = np.clip(rho, -0.999, 0.999)

        arg = -np.sqrt((nu + 1) * (1 - rho) / (1 + rho))
        lam = 2 * stats.t.cdf(arg, df=nu + 1)

        return {"lambda_lower": float(lam), "lambda_upper": float(lam)}

    elif copula_type == "clayton":
        # / lower tail only: lambda_L = 2^(-1/theta)
        theta = float(params)
        if theta <= 0:
            return {"lambda_lower": 0.0, "lambda_upper": 0.0}

        lam_lower = 2 ** (-1 / theta)
        return {"lambda_lower": float(lam_lower), "lambda_upper": 0.0}

    else:
        raise ValueError(f"unknown copula type: {copula_type}")


def simulate_copula(
    copula_type: str,
    params,
    n_samples: int,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    # / generate correlated uniform samples from fitted copula
    # / returns (n_samples, n_vars) array of uniform marginals
    if n_samples <= 0:
        raise ValueError("n_samples must be positive")
    rng = rng or np.random.default_rng()

    if copula_type == "gaussian":
        corr = np.asarray(params)
        d = corr.shape[0]
        z = rng.multivariate_normal(np.zeros(d), corr, size=n_samples)
        return stats.norm.cdf(z)

    elif copula_type == "student_t":
        nu, corr = params
        corr = np.asarray(corr)
        d = corr.shape[0]

        # / multivariate t: Z = sqrt(nu/chi2) * MVN
        z = rng.multivariate_normal(np.zeros(d), corr, size=n_samples)
        chi2 = rng.chisquare(nu, size=(n_samples, 1))
        t_samples = z * np.sqrt(nu / chi2)
        return stats.t.cdf(t_samples, df=nu)

    elif copula_type == "clayton":
        theta = float(params)
        # / bivariate Clayton simulation via conditional method
        u1 = rng.uniform(size=n_samples)
        w = rng.uniform(size=n_samples)
        # / conditional inverse: u2 = (u1^(-theta) * (w^(-theta/(1+theta)) - 1) + 1)^(-1/theta)
        u2 = (u1 ** (-theta) * (w ** (-theta / (1 + theta)) - 1) + 1) ** (-1 / theta)
        u2 = np.clip(u2, 0, 1)
        return np.column_stack([u1, u2])

    else:
        raise ValueError(f"unknown copula type: {copula_type}")


def portfolio_tail_risk(
    returns_matrix: np.ndarray,
    copula_type: str = "student_t",
    threshold: float = -0.20,
    n_simulations: int = 10_000,
    rng: np.random.Generator | None = None,
) -> dict:
    # / estimate probability of joint extreme loss using copula
    # / returns_matrix: (n_obs, n_assets) daily returns
    returns_matrix = np.asarray(returns_matrix, dtype=np.float64)

    if returns_matrix.ndim != 2:
        raise ValueError("returns_matrix must be 2D (n_obs, n_assets)")
    if returns_matrix.shape[0] < 10:
        raise ValueError(f"need at least 10 observations, got {returns_matrix.shape[0]}")

    rng = rng or np.random.default_rng()
    n_assets = returns_matrix.shape[1]

    # / convert to pseudo-observations (uniform marginals via rank transform)
    from scipy.stats import rankdata
    u_data = np.column_stack([
        rankdata(returns_matrix[:, j]) / (returns_matrix.shape[0] + 1)
        for j in range(n_assets)
    ])

    # / fit copula
    if copula_type == "student_t":
        nu, corr = student_t_copula_fit(u_data)
        params = (nu, corr)
    elif copula_type == "gaussian":
        corr = gaussian_copula_fit(u_data)
        params = corr
    elif copula_type == "clayton" and n_assets == 2:
        theta = clayton_copula_fit(u_data)
        params = theta
    else:
        raise ValueError(f"unsupported copula_type: {copula_type}")

    # / simulate from copula
    u_sim = simulate_copula(copula_type, params, n_simulations, rng)

    # / transform back to returns using empirical quantile functions
    sim_returns = np.zeros_like(u_sim)
    for j in range(n_assets):
        sorted_returns = np.sort(returns_matrix[:, j])
        indices = np.clip(
            (u_sim[:, j] * len(sorted_returns)).astype(int),
            0,
            len(sorted_returns) - 1,
        )
        sim_returns[:, j] = sorted_returns[indices]

    # / portfolio return (equal weight)
    portfolio_returns = np.mean(sim_returns, axis=1)

    # / joint extreme loss probability
    extreme_count = np.sum(portfolio_returns < threshold)
    prob = extreme_count / n_simulations

    # / tail dependence
    td = tail_dependence_coefficient(copula_type, params)

    return {
        "joint_extreme_probability": float(prob),
        "tail_dependence": td,
        "n_simulations": n_simulations,
        "threshold": threshold,
    }


def _nearest_pd(A: np.ndarray) -> np.ndarray:
    # / nearest positive definite matrix (Higham 2002)
    B = (A + A.T) / 2
    _, s, V = np.linalg.svd(B)
    H = V.T @ np.diag(np.maximum(s, 1e-6)) @ V
    A2 = (B + H) / 2
    A3 = (A2 + A2.T) / 2

    # / ensure diagonal is 1 (correlation matrix)
    d = np.sqrt(np.diag(A3))
    A3 = A3 / np.outer(d, d)
    np.fill_diagonal(A3, 1.0)

    return A3
