
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import structlog
from scipy.special import expit
from scipy.special import logit as scipy_logit

logger = structlog.get_logger(__name__)


class ParticleFilter:
    def __init__(
        self,
        n_particles: int = 1000,
        process_noise: float = 0.1,
        observation_noise: float = 0.5,
        rng: np.random.Generator | None = None,
    ):
        if n_particles <= 0:
            raise ValueError("n_particles must be positive")
        if process_noise <= 0:
            raise ValueError("process_noise must be positive")
        if observation_noise <= 0:
            raise ValueError("observation_noise must be positive")

        self.n_particles = n_particles
        self.process_noise = process_noise
        self.observation_noise = observation_noise
        self.rng = rng or np.random.default_rng()

        init_probs = self.rng.uniform(0.1, 0.9, size=n_particles)
        self._logits = scipy_logit(init_probs)
        self._weights = np.ones(n_particles) / n_particles
        self._step = 0

    def predict(self) -> None:
        noise = self.rng.normal(0, self.process_noise, size=self.n_particles)
        self._logits += noise
        self._logits = np.clip(self._logits, -10, 10)
        self._step += 1

    def update(self, observation: float, likelihood_fn: Callable[[float, float], float] | None = None) -> None:
        if likelihood_fn is not None:
            probs = expit(self._logits)
            likelihoods = np.array([
                likelihood_fn(observation, p) for p in probs
            ], dtype=np.float64)
        else:
            probs = expit(self._logits)
            likelihoods = np.exp(
                -0.5 * ((probs - observation) / self.observation_noise) ** 2
            )

        # / handle zero likelihoods
        likelihoods = np.maximum(likelihoods, 1e-300)

        # / update weights
        self._weights *= likelihoods

        # / normalize
        w_sum = np.sum(self._weights)
        if w_sum > 0:
            self._weights /= w_sum
        else:
            logger.warning("particle_weights_collapsed", step=self._step)
            self._weights = np.ones(self.n_particles) / self.n_particles

        ess = self.effective_sample_size()
        if ess < self.n_particles / 2:
            self.resample()

    def resample(self) -> None:
        n = self.n_particles
        positions = (self.rng.uniform() + np.arange(n)) / n

        cumsum = np.cumsum(self._weights)
        cumsum[-1] = 1.0

        indices = np.searchsorted(cumsum, positions)
        indices = np.clip(indices, 0, n - 1)

        self._logits = self._logits[indices].copy()
        self._weights = np.ones(n) / n

    def estimate(self) -> float:
        probs = expit(self._logits)
        return float(np.sum(self._weights * probs))

    def effective_sample_size(self) -> float:
        return float(1.0 / np.sum(self._weights ** 2))

    @property
    def particles(self) -> np.ndarray:
        # / current particle probabilities
        return np.asarray(expit(self._logits))

    @property
    def weights(self) -> np.ndarray:
        return self._weights.copy()

    @property
    def step(self) -> int:
        return self._step
