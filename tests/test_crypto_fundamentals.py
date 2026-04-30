# / tests for the crypto_fundamentals aggregator module (not the endpoint).
# / covers null-tolerance, extractor helpers, cache read/write, live fetch fallthrough.

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data import crypto_fundamentals as cf


def _mock_pool(row=None):
    pool = MagicMock()
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


class TestBaseSymbol:
    def test_strips_usd_suffix(self):
        assert cf._base_symbol("BTC-USD") == "BTC"

    def test_strips_slash_usd(self):
        assert cf._base_symbol("ETH/USD") == "ETH"

    def test_uppercases(self):
        assert cf._base_symbol("btc") == "BTC"

    def test_plain_symbol_passthrough(self):
        assert cf._base_symbol("SOL") == "SOL"


class TestExtractNVT:
    def test_hand_computed_ratio(self):
        # / mcap / vol = 1e12 / 1e10 = 100
        data = {"market_cap": 1e12, "total_volume": 1e10}
        assert cf._extract_nvt(data) == pytest.approx(100.0)

    def test_none_input(self):
        assert cf._extract_nvt(None) is None

    def test_missing_fields(self):
        assert cf._extract_nvt({"market_cap": 1e9}) is None
        assert cf._extract_nvt({"total_volume": 1e9}) is None

    def test_zero_volume_returns_none(self):
        # / avoids division by zero
        assert cf._extract_nvt({"market_cap": 1e9, "total_volume": 0}) is None

    def test_non_numeric_returns_none(self):
        assert cf._extract_nvt({"market_cap": "bad", "total_volume": 1e9}) is None


class TestExtractTVL:
    def test_unknown_chain_returns_none(self):
        assert cf._extract_tvl({"tvl": 1e9}, "BTC") is None

    def test_dict_with_tvl_key(self):
        assert cf._extract_tvl({"tvl": 50_000_000_000}, "ETH") == 50_000_000_000.0

    def test_dict_with_chain_tvls_nested(self):
        data = {"chainTvls": {"ethereum": 40_000_000_000}}
        assert cf._extract_tvl(data, "ETH") == 40_000_000_000.0

    def test_list_summed_over_matching_chains(self):
        data = [
            {"chain": "Ethereum", "tvl": 10.0},
            {"chain": "ethereum", "tvl": 20.0},
            {"chain": "solana", "tvl": 5.0},  # / not counted for ETH
        ]
        assert cf._extract_tvl(data, "ETH") == pytest.approx(30.0)

    def test_list_no_matches_returns_none(self):
        data = [{"chain": "polygon", "tvl": 100.0}]
        assert cf._extract_tvl(data, "ETH") is None

    def test_none_input(self):
        assert cf._extract_tvl(None, "ETH") is None


class TestExtractDexVolume:
    def test_total_24h_preferred(self):
        assert cf._extract_dex_volume({"total24h": 1e9, "totalVolume": 2e9}, "ETH") == 1e9

    def test_falls_back_to_totalVolume(self):
        assert cf._extract_dex_volume({"totalVolume": 5e8}, "ETH") == 5e8

    def test_none_input(self):
        assert cf._extract_dex_volume(None, "ETH") is None

    def test_non_dict_input(self):
        assert cf._extract_dex_volume([1, 2, 3], "ETH") is None


class TestExtractStablecoinRatio:
    def test_hand_computed_ratio(self):
        # / total_stable = 2.5e12 -> ratio = 2.5e12 / 2.5e12 = 1.0
        data = {"peggedAssets": [
            {"circulating": {"peggedUSD": 1.25e12}},
            {"circulating": {"peggedUSD": 1.25e12}},
        ]}
        assert cf._extract_stablecoin_ratio(data) == pytest.approx(1.0)

    def test_empty_pegged_returns_none(self):
        assert cf._extract_stablecoin_ratio({"peggedAssets": []}) is None

    def test_non_dict_returns_none(self):
        assert cf._extract_stablecoin_ratio("nope") is None

    def test_zero_total_returns_none(self):
        data = {"peggedAssets": [{"circulating": {"peggedUSD": 0}}]}
        assert cf._extract_stablecoin_ratio(data) is None


class TestExtractActiveAddresses:
    def test_reads_active_addresses_field(self):
        assert cf._extract_active_addresses([{"active_addresses": 123456}], "BTC") == 123456

    def test_falls_back_to_alt_keys(self):
        assert cf._extract_active_addresses([{"addresses": 999}], "BTC") == 999
        assert cf._extract_active_addresses([{"count": 42}], "BTC") == 42

    def test_empty_returns_none(self):
        assert cf._extract_active_addresses([], "BTC") is None
        assert cf._extract_active_addresses(None, "BTC") is None


class TestExtractExchangeInflow:
    def test_net_flow_difference(self):
        # / inflow 100M - outflow 70M = 30M
        rows = [{"inflow_usd": 100_000_000, "outflow_usd": 70_000_000}]
        assert cf._extract_exchange_inflow(rows, "BTC") == pytest.approx(30_000_000.0)

    def test_only_inflow_available(self):
        rows = [{"inflow_usd": 50.0}]
        assert cf._extract_exchange_inflow(rows, "BTC") == pytest.approx(50.0)

    def test_none_returns_none(self):
        assert cf._extract_exchange_inflow(None, "BTC") is None


class TestToFloat:
    def test_none(self):
        assert cf._to_float(None) is None

    def test_decimal(self):
        from decimal import Decimal
        assert cf._to_float(Decimal("12.5")) == 12.5

    def test_invalid_returns_none(self):
        assert cf._to_float("not a number") is None


class TestFetchLiveFundamentals:
    @pytest.mark.asyncio
    async def test_all_sources_fail_returns_nulls(self, monkeypatch):
        # / every source raises -> every field stays None, sources list empty
        monkeypatch.delenv("DUNE_API_KEY", raising=False)
        with patch("src.data.crypto_fundamentals.fetch_coin_data",
                   new_callable=AsyncMock, side_effect=Exception("cg down")), \
             patch("src.data.crypto_fundamentals.fetch_funding_rates",
                   new_callable=AsyncMock, side_effect=Exception("loris down")), \
             patch("src.data.crypto_fundamentals.fetch_defi_tvl",
                   new_callable=AsyncMock, side_effect=Exception("llama down")), \
             patch("src.data.crypto_fundamentals.fetch_dex_volume",
                   new_callable=AsyncMock, side_effect=Exception("llama down")), \
             patch("src.data.crypto_fundamentals.fetch_stablecoin_supply",
                   new_callable=AsyncMock, side_effect=Exception("llama down")):
            out = await cf.fetch_live_fundamentals("ETH-USD")
        for key in ("nvt_ratio", "funding_rate", "active_addresses",
                    "exchange_inflow_usd", "hash_rate", "tvl_usd",
                    "dex_volume_24h", "stablecoin_supply_ratio"):
            assert out[key] is None
        assert out["sources"] == []

    @pytest.mark.asyncio
    async def test_partial_success_records_source(self, monkeypatch):
        # / coingecko succeeds -> nvt set, sources contains "coingecko"
        monkeypatch.delenv("DUNE_API_KEY", raising=False)
        with patch("src.data.crypto_fundamentals.fetch_coin_data",
                   new_callable=AsyncMock,
                   return_value={"market_cap": 1e12, "total_volume": 2e10}), \
             patch("src.data.crypto_fundamentals.fetch_funding_rates",
                   new_callable=AsyncMock, return_value=None), \
             patch("src.data.crypto_fundamentals.fetch_defi_tvl",
                   new_callable=AsyncMock, return_value=None), \
             patch("src.data.crypto_fundamentals.fetch_dex_volume",
                   new_callable=AsyncMock, return_value=None), \
             patch("src.data.crypto_fundamentals.fetch_stablecoin_supply",
                   new_callable=AsyncMock, return_value=None):
            out = await cf.fetch_live_fundamentals("BTC-USD")
        assert out["nvt_ratio"] == pytest.approx(50.0)
        assert "coingecko" in out["sources"]

    @pytest.mark.asyncio
    async def test_dune_skipped_without_api_key(self, monkeypatch):
        # / no DUNE_API_KEY -> exchange flow / active addr stays None even if we provided data
        monkeypatch.delenv("DUNE_API_KEY", raising=False)
        with patch("src.data.crypto_fundamentals.fetch_coin_data",
                   new_callable=AsyncMock, return_value=None), \
             patch("src.data.crypto_fundamentals.fetch_funding_rates",
                   new_callable=AsyncMock, return_value=None), \
             patch("src.data.crypto_fundamentals.fetch_defi_tvl",
                   new_callable=AsyncMock, return_value=None), \
             patch("src.data.crypto_fundamentals.fetch_dex_volume",
                   new_callable=AsyncMock, return_value=None), \
             patch("src.data.crypto_fundamentals.fetch_stablecoin_supply",
                   new_callable=AsyncMock, return_value=None), \
             patch("src.data.crypto_fundamentals.fetch_active_addresses",
                   new_callable=AsyncMock, return_value=[{"active_addresses": 1}]):
            out = await cf.fetch_live_fundamentals("ETH-USD")
        assert out["active_addresses"] is None


class TestLoadCachedFundamentals:
    @pytest.mark.asyncio
    async def test_null_pool_returns_none(self):
        assert await cf.load_cached_fundamentals(None, "BTC") is None

    @pytest.mark.asyncio
    async def test_no_row_returns_none(self):
        pool, _ = _mock_pool(row=None)
        assert await cf.load_cached_fundamentals(pool, "BTC") is None

    @pytest.mark.asyncio
    async def test_row_returned_as_dict(self):
        row = {"symbol": "BTC", "date": date.today(), "nvt_ratio": 50.0}
        pool, _ = _mock_pool(row=row)
        out = await cf.load_cached_fundamentals(pool, "BTC")
        assert out["symbol"] == "BTC"
        assert out["nvt_ratio"] == 50.0

    @pytest.mark.asyncio
    async def test_missing_table_returns_none_silently(self):
        pool = MagicMock()
        conn = MagicMock()
        conn.fetchrow = AsyncMock(side_effect=Exception("relation \"crypto_fundamentals\" does not exist"))
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=cm)
        assert await cf.load_cached_fundamentals(pool, "BTC") is None


class TestUpsertFundamentals:
    @pytest.mark.asyncio
    async def test_null_pool_is_noop(self):
        # / should not raise
        await cf.upsert_fundamentals(None, "BTC", {"sources": []})

    @pytest.mark.asyncio
    async def test_writes_row_uppercase_symbol(self):
        pool, conn = _mock_pool()
        await cf.upsert_fundamentals(pool, "btc", {"nvt_ratio": 50.0, "sources": ["coingecko"]})
        assert conn.execute.call_count == 1
        # / first positional after SQL is symbol, upper-cased
        args = conn.execute.call_args.args
        assert args[1] == "BTC"


class TestGetFundamentals:
    @pytest.mark.asyncio
    async def test_cache_hit_short_circuits_live_fetch(self):
        row = {
            "symbol": "BTC", "date": date.today(),
            "nvt_ratio": 42.0, "funding_rate": 0.05, "active_addresses": 100,
            "exchange_inflow_usd": -50.0, "hash_rate": None, "tvl_usd": None,
            "dex_volume_24h": None, "stablecoin_supply_ratio": None,
            "sources": '["coingecko"]',
            "updated_at": datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc),
        }
        pool, _ = _mock_pool(row=row)
        with patch("src.data.crypto_fundamentals.fetch_live_fundamentals",
                   new_callable=AsyncMock) as mock_live:
            out = await cf.get_fundamentals(pool, "BTC")
        mock_live.assert_not_called()
        assert out["nvt_ratio"] == 42.0
        assert out["sources"] == ["coingecko"]

    @pytest.mark.asyncio
    async def test_cache_miss_falls_through_to_live(self):
        pool, _ = _mock_pool(row=None)
        fake_live = {
            "nvt_ratio": 10.0, "funding_rate": None, "active_addresses": None,
            "exchange_inflow_usd": None, "hash_rate": None, "tvl_usd": None,
            "dex_volume_24h": None, "stablecoin_supply_ratio": None,
            "sources": ["coingecko"],
        }
        with patch("src.data.crypto_fundamentals.fetch_live_fundamentals",
                   new_callable=AsyncMock, return_value=fake_live):
            out = await cf.get_fundamentals(pool, "BTC")
        assert out["nvt_ratio"] == 10.0
        assert "updated_at" in out
