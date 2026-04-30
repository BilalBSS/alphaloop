# / tests for shared alpaca http client

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

import src.data.alpaca_client as mod


@pytest.fixture(autouse=True)
def _reset_client():
    # / clean module-level state before and after each test
    mod._client = None
    yield
    mod._client = None


class TestAlpacaHeaders:
    def test_returns_correct_headers(self):
        with patch.dict("os.environ", {"ALPACA_API_KEY": "pk_test", "ALPACA_SECRET_KEY": "sk_test"}):
            h = mod.alpaca_headers()
        assert h["APCA-API-KEY-ID"] == "pk_test"
        assert h["APCA-API-SECRET-KEY"] == "sk_test"

    def test_returns_empty_when_env_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            h = mod.alpaca_headers()
        assert h["APCA-API-KEY-ID"] == ""
        assert h["APCA-API-SECRET-KEY"] == ""


class TestAlpacaBaseUrl:
    def test_returns_default_paper_url(self):
        with patch.dict("os.environ", {}, clear=True):
            url = mod.alpaca_base_url()
        assert url == mod.PAPER_URL

    def test_returns_custom_url_from_env(self):
        with patch.dict("os.environ", {"ALPACA_BASE_URL": "https://custom.example.com"}):
            url = mod.alpaca_base_url()
        assert url == "https://custom.example.com"


class TestGetAlpacaClient:
    @pytest.mark.asyncio
    async def test_returns_async_client(self):
        client = await mod.get_alpaca_client()
        assert isinstance(client, httpx.AsyncClient)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_returns_same_client_on_second_call(self):
        c1 = await mod.get_alpaca_client()
        c2 = await mod.get_alpaca_client()
        assert c1 is c2
        await c1.aclose()

    @pytest.mark.asyncio
    async def test_recreates_client_if_closed(self):
        c1 = await mod.get_alpaca_client()
        await c1.aclose()
        c2 = await mod.get_alpaca_client()
        assert c2 is not c1
        assert not c2.is_closed
        await c2.aclose()


class TestCloseAlpacaClient:
    @pytest.mark.asyncio
    async def test_closes_client_and_resets(self):
        client = await mod.get_alpaca_client()
        assert not client.is_closed
        await mod.close_alpaca_client()
        assert client.is_closed
        assert mod._client is None

    @pytest.mark.asyncio
    async def test_noop_when_no_client(self):
        # / should not raise when nothing to close
        assert mod._client is None
        await mod.close_alpaca_client()
        assert mod._client is None
