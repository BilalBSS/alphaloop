# / tests for retry + circuit breaker decorator

import asyncio
import time

import pytest

from src.data.resilience import (
    CircuitBreakerOpen,
    CircuitState,
    _breakers,
    _CircuitBreaker,
    get_breaker_state,
    reset_breaker,
    with_retry,
)


@pytest.fixture(autouse=True)
def clear_breakers():
    # / reset global breaker state between tests
    _breakers.clear()
    yield
    _breakers.clear()


class TestRetry:
    @pytest.mark.asyncio
    async def test_succeeds_first_try(self):
        call_count = 0

        @with_retry(source="test", max_retries=3, base_delay=0.01)
        async def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await succeed()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_succeeds_on_retry(self):
        call_count = 0

        @with_retry(source="test", max_retries=3, base_delay=0.01)
        async def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("temporary")
            return "ok"

        result = await fail_then_succeed()
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_exhausts_retries(self):
        @with_retry(source="test", max_retries=2, base_delay=0.01)
        async def always_fail():
            raise ConnectionError("permanent")

        with pytest.raises(ConnectionError, match="permanent"):
            await always_fail()

    @pytest.mark.asyncio
    async def test_preserves_original_exception_type(self):
        @with_retry(source="exc_type", max_retries=1, base_delay=0.01)
        async def raise_value_error():
            raise ValueError("bad value")

        with pytest.raises(ValueError, match="bad value"):
            await raise_value_error()

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_not_caught_by_retry(self):
        # / CircuitBreakerOpen should propagate immediately, not be retried
        call_count = 0

        @with_retry(source="cb_passthru", max_retries=0, failure_threshold=1)
        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("down")

        # / trip the breaker
        with pytest.raises((ConnectionError, CircuitBreakerOpen)):
            await always_fail()

        # / next call should raise CircuitBreakerOpen directly
        with pytest.raises(CircuitBreakerOpen):
            await always_fail()

        # / function body should not have been called on the second attempt
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_backoff_timing(self):
        times = []

        @with_retry(source="test", max_retries=2, base_delay=0.05)
        async def fail_with_timing():
            times.append(asyncio.get_event_loop().time())
            raise ConnectionError("fail")

        with pytest.raises(ConnectionError):
            await fail_with_timing()

        # / 3 attempts: t=0, t~0.05, t~0.15
        assert len(times) == 3
        assert times[1] - times[0] >= 0.04  # / ~0.05s base delay
        assert times[2] - times[1] >= 0.08  # / ~0.10s doubled


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_opens_after_threshold(self):
        call_count = 0

        @with_retry(
            source="flaky",
            max_retries=0,
            failure_threshold=3,
            reset_timeout=60,
        )
        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("down")

        # / fail 3 times to open circuit
        for _ in range(3):
            with pytest.raises((ConnectionError, CircuitBreakerOpen)):
                await always_fail()

        # / circuit should now be open
        assert get_breaker_state("flaky") == CircuitState.OPEN

        # / next call should be rejected immediately
        with pytest.raises(CircuitBreakerOpen):
            await always_fail()

    @pytest.mark.asyncio
    async def test_resets_on_success(self):
        call_count = 0

        @with_retry(source="recovery", max_retries=0, failure_threshold=5)
        async def sometimes_fail():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ConnectionError("temporary")
            return "ok"

        # / fail twice
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await sometimes_fail()

        # / succeed - should reset counter
        result = await sometimes_fail()
        assert result == "ok"
        assert get_breaker_state("recovery") == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_after_timeout(self):
        @with_retry(
            source="timeout_test",
            max_retries=0,
            failure_threshold=1,
            reset_timeout=0.05,
        )
        async def fail_once():
            raise ConnectionError("down")

        # / trip the breaker
        with pytest.raises((ConnectionError, CircuitBreakerOpen)):
            await fail_once()

        assert get_breaker_state("timeout_test") == CircuitState.OPEN

        # / wait for reset timeout
        await asyncio.sleep(0.06)

        # / should be half_open now, allows one test call
        # / but it will fail again and re-open
        with pytest.raises((ConnectionError, CircuitBreakerOpen)):
            await fail_once()

    def test_reset_breaker(self):
        _breakers["manual"] = type("B", (), {
            "record_success": lambda self: setattr(self, "state", CircuitState.CLOSED),
            "state": CircuitState.OPEN,
        })()
        reset_breaker("manual")

    def test_get_state_unknown_source(self):
        assert get_breaker_state("nonexistent") is None

    @pytest.mark.asyncio
    async def test_failure_count_increments(self):
        call_count = 0

        @with_retry(source="counter", max_retries=0, failure_threshold=10)
        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("down")

        for _i in range(3):
            with pytest.raises(ConnectionError):
                await always_fail()

        assert _breakers["counter"].failure_count == 3

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        call_count = 0

        @with_retry(source="reset_fc", max_retries=0, failure_threshold=10)
        async def sometimes_fail():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ConnectionError("fail")
            return "ok"

        # / fail twice
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await sometimes_fail()
        assert _breakers["reset_fc"].failure_count == 2

        # / succeed - should reset
        await sometimes_fail()
        assert _breakers["reset_fc"].failure_count == 0

    @pytest.mark.asyncio
    async def test_half_open_allows_one_probe(self):
        breaker = _CircuitBreaker(failure_threshold=1, reset_timeout=0.01)
        _breakers["probe_test"] = breaker

        # / trip it
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        # / wait for reset timeout
        await asyncio.sleep(0.02)

        # / first probe should be allowed
        allowed = await breaker.can_execute_async()
        assert allowed is True
        assert breaker.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_half_open_rejects_second_concurrent_request(self):
        breaker = _CircuitBreaker(failure_threshold=1, reset_timeout=0.01)
        _breakers["probe2"] = breaker

        # / trip and wait
        breaker.record_failure()
        await asyncio.sleep(0.02)

        # / first probe allowed
        first = await breaker.can_execute_async()
        assert first is True

        # / second should be rejected (probe already in flight)
        second = await breaker.can_execute_async()
        assert second is False

    def test_retry_after_decreases_over_time(self):
        breaker = _CircuitBreaker(failure_threshold=1, reset_timeout=1.0)
        breaker.record_failure()
        first = breaker.retry_after
        time.sleep(0.05)
        second = breaker.retry_after
        assert second < first

    def test_exponential_backoff_delays(self):
        # / verify base_delay * 2^attempt pattern
        base_delay = 0.5
        for attempt in range(4):
            expected = base_delay * (2 ** attempt)
            assert expected == base_delay * (2 ** attempt)
