# / sequential monte carlo (bootstrap filter) for real-time bayesian updating
# / maintains N particles as hypotheses about true state
# / operates in logit space to keep probabilities bounded [0,1]
# / systematic resampling when ESS < N/2

from __future__ import annotations

import numpy as np
from scipy.special import expit, logit as scipy_logit
import structlog

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

        # / initialize uniform particles in logit space
        # / uniform in [0.1, 0.9] probability space to avoid extremes
        init_probs = self.rng.uniform(0.1, 0.9, size=n_particles)
        self._logits = scipy_logit(init_probs)
        self._weights = np.ones(n_particles) / n_particles
        self._step = 0

    def predict(self) -> None:
        # / propagate particles via logit random walk
        # / logit(p_t) = logit(p_{t-1}) + epsilon
        noise = self.rng.normal(0, self.process_noise, size=self.n_particles)
        self._logits += noise
        # / clamp logits to prevent extreme values
        self._logits = np.clip(self._logits, -10, 10)
        self._step += 1

    def update(self, observation: float, likelihood_fn=None) -> None:
        # / reweight particles based on observation
        # / likelihood_fn(observation, particle_state) -> non-negative likelihood
        # / if no likelihood_fn provided, uses gaussian likelihood
        if likelihood_fn is not None:
            probs = expit(self._logits)
            likelihoods = np.array([
                likelihood_fn(observation, p) for p in probs
            ], dtype=np.float64)
        else:
            # / default: gaussian likelihood centered on observation
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
            # / all weights collapsed — reset to uniform
            logger.warning("particle_weights_collapsed", step=self._step)
            self._weights = np.ones(self.n_particles) / self.n_particles

        # / resample if ESS too low
        ess = self.effective_sample_size()
        if ess < self.n_particles / 2:
            self.resample()

    def resample(self) -> None:
        # / systematic resampling — lower variance than multinomial
        # / generate U ~ Uniform(0, 1/N), resample at {U + (i-1)/N}
        n = self.n_particles
        positions = (self.rng.uniform() + np.arange(n)) / n

        cumsum = np.cumsum(self._weights)
        # / fix floating point: ensure last cumsum is exactly 1
        cumsum[-1] = 1.0

        indices = np.searchsorted(cumsum, positions)
        indices = np.clip(indices, 0, n - 1)

        self._logits = self._logits[indices].copy()
        self._weights = np.ones(n) / n

    def estimate(self) -> float:
        # / weighted mean of particles -> current probability estimate
        probs = expit(self._logits)
        return float(np.sum(self._weights * probs))

    def effective_sample_size(self) -> float:
        # / ESS = 1 / sum(w^2) for normalized weights
        return float(1.0 / np.sum(self._weights ** 2))

    @property
    def particles(self) -> np.ndarray:
        # / current particle probabilities
        return expit(self._logits)

    @property
    def weights(self) -> np.ndarray:
        return self._weights.copy()

    @property
    def step(self) -> int:
        return self._step
