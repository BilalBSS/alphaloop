# / hidden markov model regime detection
# / finds latent market regimes from returns + volatility

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class HmmRegime:
    current_state: int
    state_label: str
    probabilities: list[float]
    confidence: float


def fit_hmm_regime(
    prices: pd.Series,
    n_states: int = 3,
    window: int = 252,
) -> HmmRegime | None:
    # / fit HMM on log returns + rolling volatility
    try:
        from hmmlearn.hmm import GaussianHMM
    except ImportError:
        logger.warning("hmmlearn_not_installed")
        return None

    if len(prices) < window:
        return None

    log_ret = np.log(prices / prices.shift(1)).dropna()
    vol = log_ret.rolling(20).std().dropna()

    common = log_ret.index.intersection(vol.index)
    if len(common) < 100:
        return None

    X = np.column_stack([
        log_ret.loc[common].values,
        vol.loc[common].values,
    ])

    try:
        model = GaussianHMM(
            n_components=n_states,
            covariance_type="full",
            n_iter=100,
            random_state=42,
        )
        model.fit(X[-window:])

        state_seq = model.predict(X[-window:])
        current = state_seq[-1]
        probs = model.predict_proba(X[-1:].reshape(1, -1))[0]

        # / label states by mean return
        means = model.means_[:, 0]
        sorted_states = np.argsort(means)
        label_map = {sorted_states[0]: "bear", sorted_states[1]: "sideways", sorted_states[2]: "bull"}

        return HmmRegime(
            current_state=int(current),
            state_label=label_map.get(current, "sideways"),
            probabilities=probs.tolist(),
            confidence=float(probs.max()),
        )
    except Exception as exc:
        logger.warning("hmm_fit_failed", error=str(exc))
        return None


def ensemble_regime(rule_based: str, hmm: HmmRegime | None) -> str:
    # / combine rule-based and HMM regime detection
    if hmm is None or hmm.confidence < 0.6:
        return rule_based
    if rule_based == hmm.state_label:
        return rule_based
    if hmm.confidence > 0.8:
        return hmm.state_label
    return rule_based
