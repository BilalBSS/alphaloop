# / tests for shared http client in resilience.py


from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import src.data.resilience as resilience_mod
from src.data.resilience import (
    _rate_delays,
    _rate_limiters,
    api_get,
    api_post,
    close_http_client,
    configure_rate_limit,
    get_http_client,
)


@pytest.fixture(autouse=True)
def cleanup_client():
    yield
    # / reset module state directly (sync-safe)
    if resilience_mod._http_client and not resilience_mod._http_client.is_closed:
        # / can't await in sync fixture, just null it out
        pass
    resilience_mod._http_client = None
    _rate_limiters.clear()
    _rate_delays.clear()


class TestConfigureRateLimit:
    def test_sets_semaphore_and_delay(self):
        configure_rate_limit("test_src", max_concurrent=3, delay=0.5)
        assert "test_src" in _rate_limiters
        assert _rate_delays["test_src"] == 0.5

    def test_default_values(self):
        configure_rate_limit("default_src")
        assert _rate_delays["default_src"] == 0.3


class TestGetHttpClient:
    @pytest.mark.asyncio
    async def test_creates_client(self):
        client = await get_http_client()
        assert isinstance(client, httpx.AsyncClient)
        assert not client.is_closed

    @pytest.mark.asyncio
    async def test_returns_same_client(self):
        c1 = await get_http_client()
        c2 = await get_http_client()
        assert c1 is c2

    @pytest.mark.asyncio
    async def test_recreates_after_close(self):
        c1 = await get_http_client()
        await close_http_client()
        c2 = await get_http_client()
        assert c1 is not c2


class TestCloseHttpClient:
    @pytest.mark.asyncio
    async def test_closes_client(self):
        client = await get_http_client()
        await close_http_client()
        assert client.is_closed

    @pytest.mark.asyncio
    async def test_close_when_none(self):
        # / should not raise
        resilience_mod._http_client = None
        await close_http_client()


class TestApiGet:
    @pytest.mark.asyncio
    async def test_returns_response(self):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        resilience_mod._http_client = mock_client

        resp = await api_get("https://example.com/api")
        assert resp is mock_resp
        mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_headers_and_params(self):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        resilience_mod._http_client = mock_client

        await api_get(
            "https://example.com",
            headers={"Authorization": "Bearer x"},
            params={"q": "test"},
            timeout=10.0,
        )

        _, kwargs = mock_client.get.call_args
        assert kwargs["headers"] == {"Authorization": "Bearer x"}
        assert kwargs["params"] == {"q": "test"}
        assert kwargs["timeout"] == 10.0

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("404", request=MagicMock(), response=mock_resp)
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        resilience_mod._http_client = mock_client

        with pytest.raises(httpx.HTTPStatusError):
            await api_get("https://example.com/missing")

    @pytest.mark.asyncio
    async def test_rate_limiting_applies(self):
        configure_rate_limit("limited", max_concurrent=1, delay=0.01)

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        resilience_mod._http_client = mock_client

        await api_get("https://example.com", source="limited")
        mock_client.get.assert_called_once()


class TestApiPost:
    @pytest.mark.asyncio
    async def test_posts_json(self):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_resp)

        resilience_mod._http_client = mock_client

        resp = await api_post("https://example.com/hook", json={"text": "hello"})
        assert resp is mock_resp
        _, kwargs = mock_client.post.call_args
        assert kwargs["json"] == {"text": "hello"}

    @pytest.mark.asyncio
    async def test_rate_limiting_applies(self):
        configure_rate_limit("post_limited", max_concurrent=1, delay=0.01)

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_resp)

        resilience_mod._http_client = mock_client

        await api_post("https://example.com", json={}, source="post_limited")
        mock_client.post.assert_called_once()
