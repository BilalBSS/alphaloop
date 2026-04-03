# / retry + circuit breaker decorator for external api calls
# / shared http client with per-source rate limiting
# / circuit states: closed -> open (after N failures) -> half_open (after timeout) -> closed

from __future__ import annotations

import asyncio
import time
from enum import Enum
from functools import wraps
from typing import Any, Callable

import httpx
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
        self._lock = asyncio.Lock()

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
        # / sync version for simple checks (non-concurrent use)
        return self._check_execute()

    async def can_execute_async(self) -> bool:
        # / async-safe version — serializes half_open probe selection
        async with self._lock:
            return self._check_execute()

    def _check_execute(self) -> bool:
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

            if not await breaker.can_execute_async():
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

                    if not await breaker.can_execute_async():
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


# / shared http client — reused across all data source modules
_http_client: httpx.AsyncClient | None = None
_rate_limiters: dict[str, asyncio.Semaphore] = {}
_rate_delays: dict[str, float] = {}


def configure_rate_limit(source: str, max_concurrent: int = 5, delay: float = 0.3) -> None:
    # / set concurrency + delay for a source (call at module import time)
    _rate_limiters[source] = asyncio.Semaphore(max_concurrent)
    _rate_delays[source] = delay


async def get_http_client() -> httpx.AsyncClient:
    # / lazy-init shared client
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


async def close_http_client() -> None:
    # / call on shutdown to cleanly close the shared client
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


async def _rate_limited_request(
    source: str | None,
    call: Callable[[], Any],
) -> httpx.Response:
    # / wrap an http call with optional per-source semaphore + delay
    if source and source in _rate_limiters:
        async with _rate_limiters[source]:
            delay = _rate_delays.get(source, 0)
            if delay > 0:
                await asyncio.sleep(delay)
            resp = await call()
    else:
        resp = await call()
    resp.raise_for_status()
    return resp


async def api_get(
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    source: str | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    # / shared GET with optional per-source rate limiting
    client = await get_http_client()
    return await _rate_limited_request(
        source, lambda: client.get(url, headers=headers, params=params, timeout=timeout)
    )


async def api_post(
    url: str,
    headers: dict[str, str] | None = None,
    json: Any = None,
    data: Any = None,
    source: str | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    # / shared POST with optional per-source rate limiting
    client = await get_http_client()
    return await _rate_limited_request(
        source, lambda: client.post(url, headers=headers, json=json, content=data, timeout=timeout)
    )
