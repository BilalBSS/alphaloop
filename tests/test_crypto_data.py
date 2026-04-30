# / tests for crypto data sources (coingecko + defillama)

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data.crypto_data import (
    _cg_id,
    fetch_coin_data,
    fetch_coin_market_chart,
    fetch_coin_ohlc,
    fetch_defi_tvl,
    fetch_dex_volume,
    fetch_stablecoin_supply,
)


class TestCGIdMap:
    def test_known_symbol(self):
        assert _cg_id("BTC") == "bitcoin"
        assert _cg_id("ETH") == "ethereum"

    def test_with_usd_suffix(self):
        assert _cg_id("BTC-USD") == "bitcoin"

    def test_case_insensitive(self):
        assert _cg_id("btc") == "bitcoin"

    def test_unknown_symbol(self):
        assert _cg_id("UNKNOWN") is None


class TestFetchCoinData:
    @pytest.mark.asyncio
    async def test_returns_structured_data(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "market_data": {
                "current_price": {"usd": 68200},
                "market_cap": {"usd": 1_300_000_000_000},
                "total_volume": {"usd": 25_000_000_000},
                "price_change_percentage_24h": 2.5,
                "price_change_percentage_7d": -1.2,
                "circulating_supply": 19_600_000,
            }
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("src.data.crypto_data.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_coin_data("BTC")
            assert result["price"] == 68200
            assert result["market_cap"] == 1_300_000_000_000
            assert result["symbol"] == "BTC"

    @pytest.mark.asyncio
    async def test_unknown_symbol_returns_none(self):
        result = await fetch_coin_data("FAKECOIN123")
        assert result is None


class TestFetchCoinMarketChart:
    @pytest.mark.asyncio
    async def test_returns_chart_data(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "prices": [[1700000000000, 68200]],
            "market_caps": [[1700000000000, 1.3e12]],
            "total_volumes": [[1700000000000, 25e9]],
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("src.data.crypto_data.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_coin_market_chart("BTC", days=7)
            assert len(result["prices"]) == 1
            assert result["symbol"] == "BTC"


class TestDefiLlama:
    @pytest.mark.asyncio
    async def test_fetch_tvl(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"name": "aave", "tvl": 10_000_000_000}]
        mock_resp.raise_for_status = MagicMock()

        with patch("src.data.crypto_data.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_defi_tvl()
            assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_fetch_tvl_specific_protocol(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"name": "aave", "tvl": 10e9}
        mock_resp.raise_for_status = MagicMock()

        with patch("src.data.crypto_data.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_defi_tvl(protocol="aave")
            assert result["name"] == "aave"

    @pytest.mark.asyncio
    async def test_fetch_dex_volume(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"totalVolume": 5_000_000_000}
        mock_resp.raise_for_status = MagicMock()

        with patch("src.data.crypto_data.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_dex_volume()
            assert "totalVolume" in result

    @pytest.mark.asyncio
    async def test_fetch_stablecoin_supply(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"peggedAssets": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("src.data.crypto_data.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_stablecoin_supply()
            assert "peggedAssets" in result


class TestFetchCoinOhlc:
    @pytest.mark.asyncio
    async def test_returns_candles(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            [1700000000000, 35000.0, 35500.0, 34800.0, 35200.0],
            [1700014400000, 35200.0, 35800.0, 35100.0, 35600.0],
        ]
        mock_resp.raise_for_status = MagicMock()

        with patch("src.data.crypto_data.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_coin_ohlc("BTC-USD", days=7)
            assert result is not None
            assert len(result) == 2
            assert result[0]["open"] == 35000.0
            assert result[1]["close"] == 35600.0

    @pytest.mark.asyncio
    async def test_unknown_symbol_returns_none(self):
        result = await fetch_coin_ohlc("UNKNOWN-USD")
        assert result is None

    @pytest.mark.asyncio
    async def test_non_list_response_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "rate limit"}
        mock_resp.raise_for_status = MagicMock()

        with patch("src.data.crypto_data.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_coin_ohlc("ETH-USD")
            assert result is None
