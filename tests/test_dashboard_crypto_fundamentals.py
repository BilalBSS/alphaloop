# / tests for /api/crypto-fundamentals/{symbol} + the live aggregator.
# / verifies null-tolerance (one source failure returns null for that field only),
# / non-crypto symbols get rejected, and cache hits short-circuit the live fetch.

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_pool(row=None):
    # / asyncpg pool mock — fetchrow returns the cached row shape when provided
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = row
    mock_conn.execute.return_value = "INSERT 0 1"
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool, mock_conn


async def _client():
    from httpx import ASGITransport, AsyncClient

    from src.dashboard import app as dashboard
    return AsyncClient(transport=ASGITransport(app=dashboard.app), base_url="http://test")


EXPECTED_KEYS = {
    "nvt_ratio", "funding_rate", "active_addresses", "exchange_inflow_usd",
    "hash_rate", "tvl_usd", "dex_volume_24h", "stablecoin_supply_ratio",
    "sources", "updated_at",
}


class TestEndpointShape:
    @pytest.mark.asyncio
    async def test_returns_all_keys_on_empty_cache(self):
        from src.dashboard import app as dashboard
        pool, _ = _mock_pool(row=None)
        dashboard.STATE.pool = pool
        with patch("src.data.crypto_fundamentals.fetch_live_fundamentals",
                   new_callable=AsyncMock, return_value={
                       "nvt_ratio": 12.3, "funding_rate": 0.05,
                       "active_addresses": 1_200_000, "exchange_inflow_usd": -50_000_000,
                       "hash_rate": None, "tvl_usd": 80_000_000_000,
                       "dex_volume_24h": 1_500_000_000, "stablecoin_supply_ratio": 0.058,
                       "sources": ["coingecko", "defillama"],
                   }):
            async with await _client() as c:
                resp = await c.get("/api/crypto-fundamentals/BTC-USD")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == EXPECTED_KEYS
        assert data["nvt_ratio"] == 12.3
        assert data["hash_rate"] is None
        assert data["sources"] == ["coingecko", "defillama"]
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_non_crypto_symbol_400s(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/crypto-fundamentals/AAPL")
        assert resp.status_code == 400
        assert "error" in resp.json()


class TestNullTolerance:
    @pytest.mark.asyncio
    async def test_one_source_failure_does_not_500(self):
        # / coingecko works, loris throws, defillama works, dune is gated off.
        # / endpoint must return 200 with funding_rate=null, nvt + tvl populated.
        from src.dashboard import app as dashboard
        pool, _ = _mock_pool(row=None)
        dashboard.STATE.pool = pool

        fake_coin = {"market_cap": 1_000_000_000, "total_volume": 50_000_000}
        fake_tvl_list = [{"chain": "ethereum", "tvl": 70_000_000_000}]

        with patch("src.data.crypto_fundamentals.fetch_coin_data",
                   new_callable=AsyncMock, return_value=fake_coin), \
             patch("src.data.crypto_fundamentals.fetch_funding_rates",
                   new_callable=AsyncMock, side_effect=RuntimeError("loris down")), \
             patch("src.data.crypto_fundamentals.fetch_defi_tvl",
                   new_callable=AsyncMock, return_value=fake_tvl_list), \
             patch("src.data.crypto_fundamentals.fetch_dex_volume",
                   new_callable=AsyncMock, return_value={"total24h": 2_500_000_000}), \
             patch("src.data.crypto_fundamentals.fetch_stablecoin_supply",
                   new_callable=AsyncMock, side_effect=RuntimeError("llama 500")), \
             patch.dict("os.environ", {}, clear=False):
            # / make sure DUNE_API_KEY is absent so dune is skipped silently
            import os as _os
            _os.environ.pop("DUNE_API_KEY", None)
            async with await _client() as c:
                resp = await c.get("/api/crypto-fundamentals/ETH-USD")

        assert resp.status_code == 200
        data = resp.json()
        # / coingecko fed nvt = 1e9 / 5e7 = 20.0
        assert data["nvt_ratio"] == pytest.approx(20.0, abs=0.01)
        # / loris failure leaves funding_rate null
        assert data["funding_rate"] is None
        # / defillama /protocols path summed ethereum tvl
        assert data["tvl_usd"] == 70_000_000_000
        assert data["dex_volume_24h"] == 2_500_000_000
        # / stablecoin source failed -> null for that field
        assert data["stablecoin_supply_ratio"] is None
        # / dune skipped (no key) -> addresses/inflow null
        assert data["active_addresses"] is None
        assert data["exchange_inflow_usd"] is None
        # / sources recorded per successful provider
        assert "coingecko" in data["sources"]
        assert "defillama" in data["sources"]
        assert "loris" not in data["sources"]
        assert "dune" not in data["sources"]
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_total_failure_still_returns_shape(self):
        # / if every source throws we still return 200 with all nulls.
        from src.dashboard import app as dashboard
        pool, _ = _mock_pool(row=None)
        dashboard.STATE.pool = pool

        with patch("src.data.crypto_fundamentals.fetch_coin_data",
                   new_callable=AsyncMock, side_effect=RuntimeError("boom")), \
             patch("src.data.crypto_fundamentals.fetch_funding_rates",
                   new_callable=AsyncMock, side_effect=RuntimeError("boom")), \
             patch("src.data.crypto_fundamentals.fetch_defi_tvl",
                   new_callable=AsyncMock, side_effect=RuntimeError("boom")), \
             patch("src.data.crypto_fundamentals.fetch_dex_volume",
                   new_callable=AsyncMock, side_effect=RuntimeError("boom")), \
             patch("src.data.crypto_fundamentals.fetch_stablecoin_supply",
                   new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            import os as _os
            _os.environ.pop("DUNE_API_KEY", None)
            async with await _client() as c:
                resp = await c.get("/api/crypto-fundamentals/SOL-USD")

        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == EXPECTED_KEYS
        for key in ("nvt_ratio", "funding_rate", "active_addresses",
                    "exchange_inflow_usd", "hash_rate", "tvl_usd",
                    "dex_volume_24h", "stablecoin_supply_ratio"):
            assert data[key] is None, f"{key} should be null after total failure"
        assert data["sources"] == []
        dashboard.STATE.pool = None


class TestCacheShortCircuit:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_live_fetch(self):
        # / when crypto_fundamentals has a recent row, endpoint returns it without
        # / calling the live fetchers at all
        from src.dashboard import app as dashboard
        cached_row = {
            "symbol": "BTC-USD",
            "date": date(2026, 4, 18),
            "nvt_ratio": Decimal("14.25"),
            "funding_rate": Decimal("0.082000"),
            "active_addresses": 905_000,
            "exchange_inflow_usd": Decimal("-12500000.00"),
            "hash_rate": None,
            "tvl_usd": None,
            "dex_volume_24h": Decimal("3200000000.00"),
            "stablecoin_supply_ratio": Decimal("0.061234"),
            "sources": '["coingecko", "loris", "defillama"]',
            "updated_at": datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc),
        }
        pool, _ = _mock_pool(row=cached_row)
        dashboard.STATE.pool = pool

        with patch("src.data.crypto_fundamentals.fetch_live_fundamentals",
                   new_callable=AsyncMock) as live:
            async with await _client() as c:
                resp = await c.get("/api/crypto-fundamentals/BTC-USD")
            live.assert_not_called()

        assert resp.status_code == 200
        data = resp.json()
        assert data["nvt_ratio"] == 14.25
        assert data["funding_rate"] == pytest.approx(0.082, abs=1e-6)
        assert data["active_addresses"] == 905_000
        assert data["exchange_inflow_usd"] == -12_500_000.0
        assert data["hash_rate"] is None
        assert data["sources"] == ["coingecko", "loris", "defillama"]
        assert data["updated_at"].startswith("2026-04-18")
        dashboard.STATE.pool = None


class TestAggregatorExtractors:
    def test_nvt_handles_missing_volume(self):
        from src.data.crypto_fundamentals import _extract_nvt
        assert _extract_nvt(None) is None
        assert _extract_nvt({"market_cap": 1e9, "total_volume": 0}) is None
        assert _extract_nvt({"market_cap": 1e9, "total_volume": None}) is None

    def test_nvt_happy_path(self):
        from src.data.crypto_fundamentals import _extract_nvt
        assert _extract_nvt({"market_cap": 1e9, "total_volume": 5e7}) == 20.0

    def test_tvl_list_sums_matching_chain(self):
        from src.data.crypto_fundamentals import _extract_tvl
        rows = [
            {"chain": "Ethereum", "tvl": 50e9},
            {"chain": "ethereum", "tvl": 20e9},
            {"chain": "arbitrum", "tvl": 5e9},
        ]
        # / ETH symbol maps to "ethereum" — sums both rows case-insensitively
        val = _extract_tvl(rows, "ETH")
        assert val == 70_000_000_000

    def test_tvl_returns_null_for_pure_l1(self):
        from src.data.crypto_fundamentals import _extract_tvl
        # / BTC has no defillama chain mapping
        assert _extract_tvl([{"chain": "bitcoin", "tvl": 1e9}], "BTC") is None

    def test_dex_volume_prefers_total24h(self):
        from src.data.crypto_fundamentals import _extract_dex_volume
        assert _extract_dex_volume({"total24h": 123.0, "totalVolume": 456.0}, "ETH") == 123.0
        assert _extract_dex_volume({"totalVolume": 456.0}, "ETH") == 456.0
        assert _extract_dex_volume({}, "ETH") is None
        assert _extract_dex_volume(None, "ETH") is None
