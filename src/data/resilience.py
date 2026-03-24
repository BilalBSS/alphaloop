# / retry + circuit breaker decorator for external api calls
# / circuit states: closed -> open (after N failures) -> half_open (after timeout) -> closed

from __future__ import annotations

import asyncio
import time
from enum import Enum
from functools import wraps
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(Exception):
    # / raised when call rejected because circuit is open
    def __init__(self, source: str, retry_after: float):
        self.source = source
        self.retry_after = retry_after
        super().__init__(f"circuit open for '{source}', retry in {retry_after:.0f}s")


class _CircuitBreaker:
    # / per-source circuit breaker state

    def __init__(self, failure_threshold: int, reset_timeout: float):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: float = 0.0
        self._half_open_probe_in_flight = False

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        self._half_open_probe_in_flight = False
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.reset_timeout:
                self.state = CircuitState.HALF_OPEN
                # / fall through to half_open check
            else:
                return False
        # / half_open: allow exactly one probe request
        if self.state == CircuitState.HALF_OPEN:
            if self._half_open_probe_in_flight:
                return False
            self._half_open_probe_in_flight = True
            return True
        return False

    @property
    def retry_after(self) -> float:
        elapsed = time.monotonic() - self.last_failure_time
        return max(0.0, self.reset_timeout - elapsed)


# / global registry of breakers keyed by source name
_breakers: dict[str, _CircuitBreaker] = {}


def with_retry(
    source: str,
    max_retries: int = 3,
    base_delay: float = 1.0,
    failure_threshold: int = 5,
    reset_timeout: float = 900.0,
) -> Callable:
    # / decorator: exponential backoff retry + circuit breaker
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if source not in _breakers:
                _breakers[source] = _CircuitBreaker(failure_threshold, reset_timeout)
            breaker = _breakers[source]

            if not breaker.can_execute():
                raise CircuitBreakerOpen(source, breaker.retry_after)

            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    result = await fn(*args, **kwargs)
                    breaker.record_success()
                    return result
                except CircuitBreakerOpen:
                    raise
                except Exception as exc:
                    last_exc = exc
                    breaker.record_failure()

                    if not breaker.can_execute():
                        logger.warning(
                            "circuit_breaker_opened",
                            source=source,
                            failure_count=breaker.failure_count,
                        )
                        raise CircuitBreakerOpen(source, breaker.retry_after) from exc

                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            "retrying",
                            source=source,
                            attempt=attempt + 1,
                            delay=delay,
                            error=str(exc),
                        )
                        await asyncio.sleep(delay)

            raise last_exc  # type: ignore[misc]

        return wrapper
    return decorator


def reset_breaker(source: str) -> None:
    # / manually reset a circuit breaker
    if source in _breakers:
        _breakers[source].record_success()


def get_breaker_state(source: str) -> CircuitState | None:
    # / get current state, none if not created yet
    return _breakers[source].state if source in _breakers else None
