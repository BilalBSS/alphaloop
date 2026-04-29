# / tests for dune analytics on-chain data

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data.crypto_onchain import (
    _dune_headers,
    execute_query,
    fetch_active_addresses,
    fetch_exchange_flows,
    fetch_whale_transactions,
    poll_results,
)


class TestDuneHeaders:
    def test_includes_api_key(self):
        with patch.dict(os.environ, {"DUNE_API_KEY": "test_key"}):
            headers = _dune_headers()
            assert headers["x-dune-api-key"] == "test_key"

    def test_empty_when_no_key(self):
        with patch.dict(os.environ, {}, clear=True):
            headers = _dune_headers()
            assert headers["x-dune-api-key"] == ""


class TestExecuteQuery:
    @pytest.mark.asyncio
    async def test_returns_execution_id(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"execution_id": "exec_123"}
        mock_resp.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"DUNE_API_KEY": "key"}):
            with patch("src.data.crypto_onchain.api_post", new_callable=AsyncMock, return_value=mock_resp):
                result = await execute_query(12345)
                assert result == "exec_123"

    @pytest.mark.asyncio
    async def test_returns_none_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            result = await execute_query(12345)
            assert result is None


class TestPollResults:
    @pytest.mark.asyncio
    async def test_returns_rows_on_complete(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "state": "QUERY_STATE_COMPLETED",
            "result": {"rows": [{"address": "0x123", "value": 100}]},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"DUNE_API_KEY": "key"}):
            with patch("src.data.crypto_onchain.api_get", new_callable=AsyncMock, return_value=mock_resp):
                result = await poll_results("exec_123", max_polls=1)
                assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"state": "QUERY_STATE_FAILED"}
        mock_resp.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"DUNE_API_KEY": "key"}):
            with patch("src.data.crypto_onchain.api_get", new_callable=AsyncMock, return_value=mock_resp):
                result = await poll_results("exec_123", max_polls=1)
                assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            result = await poll_results("exec_123", max_polls=1)
            assert result == []


class TestConvenienceFunctions:
    @pytest.mark.asyncio
    async def test_fetch_active_addresses_no_query(self):
        with patch.dict(os.environ, {"DUNE_API_KEY": "key", "DUNE_QUERY_ACTIVE_ADDRESSES": "0"}):
            result = await fetch_active_addresses()
            assert result == []

    @pytest.mark.asyncio
    async def test_fetch_exchange_flows_no_query(self):
        with patch.dict(os.environ, {"DUNE_API_KEY": "key", "DUNE_QUERY_EXCHANGE_FLOWS": "0"}):
            result = await fetch_exchange_flows()
            assert result == []

    @pytest.mark.asyncio
    async def test_fetch_whale_transactions_no_query(self):
        with patch.dict(os.environ, {"DUNE_API_KEY": "key", "DUNE_QUERY_WHALE_TRANSACTIONS": "0"}):
            result = await fetch_whale_transactions()
            assert result == []
