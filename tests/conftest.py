# / shared fixtures for test suite

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_pool():
    # / standard asyncpg pool mock — conn accessible via pool.acquire().__aenter__
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool, mock_conn


@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    # / prevent circuit breaker state leaking between tests
    yield
    from src.data.resilience import _breakers
    _breakers.clear()


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    # / prevent rate limiter state leaking between tests
    yield
    from src.data.resilience import _rate_delays, _rate_limiters
    _rate_limiters.clear()
    _rate_delays.clear()


@pytest.fixture(autouse=True)
def reset_alpaca_client():
    # / prevent shared alpaca client leaking between tests
    yield
    import src.data.alpaca_client as mod
    mod._client = None


@pytest.fixture(autouse=True)
def reset_llm_clients():
    # / prevent shared llm clients leaking between tests
    yield
    import src.data.llm_client as mod
    mod._clients.clear()


@pytest.fixture(autouse=True)
def reset_intraday_cache():
    # / prevent /api/intraday ttl cache leaking between tests
    yield
    try:
        from src.dashboard.app import _intraday_cache_clear
        _intraday_cache_clear()
    except ImportError:
        pass
