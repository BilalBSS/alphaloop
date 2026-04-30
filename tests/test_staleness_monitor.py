# / tests for data source staleness monitor

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.data.staleness_monitor import (
    FRESHNESS_THRESHOLDS,
    SourceFreshness,
    check_all_freshness,
)


def _mock_pool(last_update_map: dict[str, datetime | None] | None = None,
               error_sources: set[str] | None = None):
    # / map source_name -> datetime to return from fetchrow; unmapped sources return None
    last_update_map = last_update_map or {}
    error_sources = error_sources or set()
    pool = MagicMock()
    conn = MagicMock()

    async def fake_fetchrow(query: str):
        # / figure out which source this is based on the table name in the query
        for source in FRESHNESS_THRESHOLDS:
            # / the per-source query references the matching table — match the SELECT target
            if source == "market_data" and "FROM market_data " in query:
                if "market_data" in error_sources:
                    raise Exception("boom")
                return [last_update_map.get("market_data")]
            if source == "market_data_crypto" and "market_data_intraday" in query:
                if "market_data_crypto" in error_sources:
                    raise Exception("boom")
                return [last_update_map.get("market_data_crypto")]
            if source not in ("market_data", "market_data_crypto") and f"FROM {source}" in query:
                if source in error_sources:
                    raise Exception("boom")
                return [last_update_map.get(source)]
        return [None]

    conn.fetchrow = AsyncMock(side_effect=fake_fetchrow)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


class TestFreshnessThresholds:
    def test_contains_all_core_sources(self):
        expected = {
            "market_data", "market_data_crypto", "fundamentals",
            "insider_trades", "news_sentiment", "social_sentiment",
            "regime_history", "analysis_scores", "computed_indicators",
        }
        assert expected.issubset(set(FRESHNESS_THRESHOLDS.keys()))

    def test_all_thresholds_positive_hours(self):
        for source, hours in FRESHNESS_THRESHOLDS.items():
            assert hours > 0, f"{source} must have a positive threshold"

    def test_market_data_buffer_matches_trading_day(self):
        # / 28h = trading day + buffer documented in module comment
        assert FRESHNESS_THRESHOLDS["market_data"] == 28

    def test_crypto_tighter_than_equity(self):
        # / crypto runs 24/7, equity gets a weekend buffer
        assert FRESHNESS_THRESHOLDS["market_data_crypto"] < FRESHNESS_THRESHOLDS["market_data"]


class TestSourceFreshnessDataclass:
    def test_fields(self):
        now = datetime.now(timezone.utc)
        f = SourceFreshness(
            source="market_data", last_update=now,
            staleness_hours=1.5, threshold_hours=28, is_stale=False,
        )
        assert f.source == "market_data"
        assert f.staleness_hours == 1.5
        assert f.is_stale is False


class TestCheckAllFreshness:
    @pytest.mark.asyncio
    async def test_returns_entry_per_source(self):
        pool, _ = _mock_pool()
        out = await check_all_freshness(pool)
        # / one entry per key in queries map (same count as threshold keys)
        assert len(out) == len(FRESHNESS_THRESHOLDS)
        names = {r.source for r in out}
        assert "market_data" in names
        assert "regime_history" in names

    @pytest.mark.asyncio
    async def test_null_update_marks_stale_with_inf_hours(self):
        pool, _ = _mock_pool(last_update_map={})  # / all None
        out = await check_all_freshness(pool)
        for r in out:
            assert r.last_update is None
            assert r.staleness_hours == float("inf")
            assert r.is_stale is True

    @pytest.mark.asyncio
    async def test_fresh_data_not_marked_stale(self):
        # / 1 hour ago < 28h threshold on market_data
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        pool, _ = _mock_pool(last_update_map={"market_data": recent})
        out = await check_all_freshness(pool)
        md = next(r for r in out if r.source == "market_data")
        assert md.is_stale is False
        assert md.staleness_hours < 2.0

    @pytest.mark.asyncio
    async def test_old_data_marked_stale(self):
        # / market_data threshold is 28h. 50h ago = stale
        old = datetime.now(timezone.utc) - timedelta(hours=50)
        pool, _ = _mock_pool(last_update_map={"market_data": old})
        out = await check_all_freshness(pool)
        md = next(r for r in out if r.source == "market_data")
        assert md.is_stale is True
        assert md.staleness_hours >= 49.0

    @pytest.mark.asyncio
    async def test_boundary_exactly_at_threshold_not_stale(self):
        # / is_stale requires strictly greater — exactly at threshold is fine
        boundary = datetime.now(timezone.utc) - timedelta(hours=FRESHNESS_THRESHOLDS["market_data"] - 0.1)
        pool, _ = _mock_pool(last_update_map={"market_data": boundary})
        out = await check_all_freshness(pool)
        md = next(r for r in out if r.source == "market_data")
        assert md.is_stale is False

    @pytest.mark.asyncio
    async def test_naive_datetime_gets_utc_tz_attached(self):
        # / when last_update.tzinfo is None, code adds UTC — must not crash
        naive = datetime.utcnow() - timedelta(hours=1)
        pool, _ = _mock_pool(last_update_map={"market_data": naive})
        out = await check_all_freshness(pool)
        md = next(r for r in out if r.source == "market_data")
        assert md.last_update is not None
        assert md.last_update.tzinfo is not None

    @pytest.mark.asyncio
    async def test_query_error_yields_stale_entry(self):
        pool, _ = _mock_pool(error_sources={"market_data"})
        out = await check_all_freshness(pool)
        md = next(r for r in out if r.source == "market_data")
        assert md.is_stale is True
        assert md.staleness_hours == float("inf")
        assert md.last_update is None

    @pytest.mark.asyncio
    async def test_threshold_populated_from_map(self):
        pool, _ = _mock_pool()
        out = await check_all_freshness(pool)
        for r in out:
            assert r.threshold_hours == FRESHNESS_THRESHOLDS[r.source]

    @pytest.mark.asyncio
    async def test_staleness_hours_rounded_to_one_decimal(self):
        recent = datetime.now(timezone.utc) - timedelta(minutes=90)  # / 1.5 hours
        pool, _ = _mock_pool(last_update_map={"market_data": recent})
        out = await check_all_freshness(pool)
        md = next(r for r in out if r.source == "market_data")
        # / round to 1 decimal -> 1.5
        assert md.staleness_hours == round(md.staleness_hours, 1)
