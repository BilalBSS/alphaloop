# / tests for dashboard marker_aggregator (unified timeline marker fetch)

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.dashboard.marker_aggregator import (
    _market_for_symbol,
    build_markers,
    fetch_consensus_strip,
    fetch_earnings_markers,
    fetch_insider_markers,
    fetch_regime_bands,
    fetch_signal_markers,
    fetch_trade_markers,
)


def _mock_pool():
    pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="OK")
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


def test_market_for_symbol_equity():
    assert _market_for_symbol("AAPL") == "equity"
    assert _market_for_symbol("msft") == "equity"
    assert _market_for_symbol("") == "equity"


def test_market_for_symbol_crypto_suffixes():
    assert _market_for_symbol("BTC-USD") == "crypto"
    assert _market_for_symbol("eth-usd") == "crypto"
    assert _market_for_symbol("BTC/USD") == "crypto"
    assert _market_for_symbol("BTCUSDT") == "crypto"


def test_market_for_symbol_non_string_defaults_to_equity():
    assert _market_for_symbol(None) == "equity"
    assert _market_for_symbol(42) == "equity"


@pytest.mark.asyncio
async def test_fetch_trade_markers_normalizes_long_to_buy():
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 19, tzinfo=timezone.utc)
    conn.fetch = AsyncMock(return_value=[
        {"created_at": ts, "symbol": "AAPL", "side": "long",
         "price": 100.0, "strategy_id": 1, "pnl": 5.0},
        {"created_at": ts, "symbol": "AAPL", "side": "SELL",
         "price": 101.0, "strategy_id": 1, "pnl": -2.0},
    ])
    out = await fetch_trade_markers(pool, "AAPL", timedelta(days=30))
    assert out[0]["side"] == "buy"
    assert out[1]["side"] == "sell"
    assert out[0]["price"] == 100.0


@pytest.mark.asyncio
async def test_fetch_trade_markers_db_error_returns_empty():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(side_effect=Exception("db dead"))
    out = await fetch_trade_markers(pool, "AAPL", timedelta(days=30))
    assert out == []


@pytest.mark.asyncio
async def test_fetch_signal_markers_filters_below_strength_threshold():
    # / threshold is 0.5 — anything lower must be dropped
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 19, tzinfo=timezone.utc)
    conn.fetch = AsyncMock(return_value=[
        {"created_at": ts, "signal_type": "buy", "strength": 0.4, "strategy_id": 1},
        {"created_at": ts, "signal_type": "buy", "strength": 0.5, "strategy_id": 1},
        {"created_at": ts, "signal_type": "sell", "strength": 0.9, "strategy_id": 2},
        {"created_at": ts, "signal_type": "buy", "strength": None, "strategy_id": 3},
    ])
    out = await fetch_signal_markers(pool, "AAPL", timedelta(days=30))
    # / 0.4 dropped (below); 0.5 kept (at boundary); 0.9 kept; None dropped
    assert len(out) == 2
    assert out[0]["strength"] == 0.5
    assert out[1]["strength"] == 0.9


@pytest.mark.asyncio
async def test_fetch_signal_markers_db_error():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(side_effect=Exception("dead"))
    assert await fetch_signal_markers(pool, "AAPL", timedelta(days=30)) == []


@pytest.mark.asyncio
async def test_fetch_insider_markers_drops_unknown_transaction_types():
    pool, conn = _mock_pool()
    d = date(2026, 4, 19)
    conn.fetch = AsyncMock(return_value=[
        {"filing_date": d, "insider_name": "CEO",
         "transaction_type": "grant", "shares": 100.0},
        {"filing_date": d, "insider_name": "CFO",
         "transaction_type": None, "shares": 50.0},
    ])
    out = await fetch_insider_markers(pool, "AAPL", timedelta(days=30))
    # / both dropped — neither buy nor sell
    assert out == []


@pytest.mark.asyncio
async def test_fetch_insider_markers_clusters_three_within_five_days():
    # / 3 buys within 5 days => cluster of 3 collapses to one marker with summed shares
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[
        {"filing_date": date(2026, 4, 10), "insider_name": "CEO",
         "transaction_type": "buy", "shares": 100.0},
        {"filing_date": date(2026, 4, 12), "insider_name": "CFO",
         "transaction_type": "buy", "shares": 200.0},
        {"filing_date": date(2026, 4, 14), "insider_name": "COO",
         "transaction_type": "buy", "shares": 300.0},
    ])
    out = await fetch_insider_markers(pool, "AAPL", timedelta(days=30))
    assert len(out) == 1
    assert out[0]["cluster_size"] == 3
    assert out[0]["shares"] == 600.0


@pytest.mark.asyncio
async def test_fetch_insider_markers_two_events_not_clustered():
    # / only 2 events within 5 days — below min cluster size, emit individually
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[
        {"filing_date": date(2026, 4, 10), "insider_name": "CEO",
         "transaction_type": "buy", "shares": 100.0},
        {"filing_date": date(2026, 4, 12), "insider_name": "CFO",
         "transaction_type": "buy", "shares": 200.0},
    ])
    out = await fetch_insider_markers(pool, "AAPL", timedelta(days=30))
    assert len(out) == 2
    assert all(r["cluster_size"] == 1 for r in out)


@pytest.mark.asyncio
async def test_fetch_insider_markers_mixed_types_do_not_cluster():
    # / 2 buys + 1 sell within window should NOT form a cluster (different types)
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[
        {"filing_date": date(2026, 4, 10), "insider_name": "CEO",
         "transaction_type": "buy", "shares": 100.0},
        {"filing_date": date(2026, 4, 12), "insider_name": "CFO",
         "transaction_type": "sell", "shares": 200.0},
        {"filing_date": date(2026, 4, 14), "insider_name": "COO",
         "transaction_type": "buy", "shares": 300.0},
    ])
    out = await fetch_insider_markers(pool, "AAPL", timedelta(days=30))
    # / 3 rows all emitted individually (no cluster of same-type reached min 3)
    assert len(out) == 3


@pytest.mark.asyncio
async def test_fetch_earnings_markers_classifies_beat_miss_inline():
    # / first row has no prior -> inline
    # / second row same period: 1.10 vs prior 1.00 -> 10% up -> beat
    # / third row same period: 0.95 vs prior 1.10 -> down -> miss
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[
        {"estimate_date": date(2026, 4, 10), "period": "Q2_2026",
         "eps_estimate": 1.00, "revenue_estimate": 1000},
        {"estimate_date": date(2026, 4, 12), "period": "Q2_2026",
         "eps_estimate": 1.10, "revenue_estimate": 1100},
        {"estimate_date": date(2026, 4, 14), "period": "Q2_2026",
         "eps_estimate": 0.95, "revenue_estimate": 950},
    ])
    out = await fetch_earnings_markers(pool, "AAPL", timedelta(days=30))
    assert out[0]["type"] == "inline"
    assert out[1]["type"] == "beat"
    assert out[2]["type"] == "miss"


@pytest.mark.asyncio
async def test_fetch_earnings_markers_tight_change_is_inline():
    # / <1% change (0.5%) should count as inline, not beat/miss
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[
        {"estimate_date": date(2026, 4, 10), "period": "Q2",
         "eps_estimate": 1.00, "revenue_estimate": 1000},
        {"estimate_date": date(2026, 4, 12), "period": "Q2",
         "eps_estimate": 1.005, "revenue_estimate": 1005},
    ])
    out = await fetch_earnings_markers(pool, "AAPL", timedelta(days=30))
    assert out[1]["type"] == "inline"


@pytest.mark.asyncio
async def test_fetch_regime_bands_collapses_consecutive_days():
    # / 3 bull days + 2 bear days -> 2 bands
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[
        {"date": date(2026, 4, 1), "regime": "bull"},
        {"date": date(2026, 4, 2), "regime": "bull"},
        {"date": date(2026, 4, 3), "regime": "bull"},
        {"date": date(2026, 4, 4), "regime": "bear"},
        {"date": date(2026, 4, 5), "regime": "bear"},
    ])
    out = await fetch_regime_bands(pool, "AAPL", timedelta(days=30))
    assert len(out) == 2
    assert out[0]["regime"] == "bull"
    assert out[1]["regime"] == "bear"


@pytest.mark.asyncio
async def test_fetch_regime_bands_routes_crypto_market():
    # / verify BTC-USD sends market='crypto' to the query
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[])
    await fetch_regime_bands(pool, "BTC-USD", timedelta(days=30))
    args = conn.fetch.call_args.args
    assert args[1] == "crypto"


@pytest.mark.asyncio
async def test_fetch_regime_bands_routes_equity_market():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[])
    await fetch_regime_bands(pool, "AAPL", timedelta(days=30))
    args = conn.fetch.call_args.args
    assert args[1] == "equity"


@pytest.mark.asyncio
async def test_fetch_consensus_strip_filters_invalid_values():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[
        {"date": date(2026, 4, 1), "consensus": "bullish"},
        {"date": date(2026, 4, 2), "consensus": "unknown_state"},
        {"date": date(2026, 4, 3), "consensus": None},
        {"date": date(2026, 4, 4), "consensus": "disagree"},
    ])
    out = await fetch_consensus_strip(pool, "AAPL", timedelta(days=30))
    assert len(out) == 2
    assert out[0]["consensus"] == "bullish"
    assert out[1]["consensus"] == "disagree"


@pytest.mark.asyncio
async def test_build_markers_empty_kinds_returns_empty_dict():
    pool, _conn = _mock_pool()
    out = await build_markers(pool, "AAPL", set(), days=30)
    assert out == {}


@pytest.mark.asyncio
async def test_build_markers_runs_selected_kinds_in_parallel():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[])
    out = await build_markers(pool, "AAPL", {"trades", "signals", "regime"}, days=30)
    # / only the 3 requested kinds populated, no others
    assert set(out.keys()) == {"trades", "signals", "regime"}
    assert all(v == [] for v in out.values())


@pytest.mark.asyncio
async def test_build_markers_passes_timedelta_not_string():
    # / regression: asyncpg rejects str for INTERVAL params
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[])
    await build_markers(pool, "AAPL", {"trades"}, days=30)
    args = conn.fetch.call_args.args
    # / the interval arg must be a timedelta object
    assert isinstance(args[2], timedelta)
    assert args[2] == timedelta(days=30)


@pytest.mark.asyncio
async def test_build_markers_handles_individual_failure_gracefully():
    # / one fetch raises -> that kind empty, others still succeed
    pool, conn = _mock_pool()
    # / trades call raises; signals call returns []
    conn.fetch = AsyncMock(side_effect=[Exception("trades dead"), []])
    out = await build_markers(pool, "AAPL", {"trades", "signals"}, days=30)
    assert out["trades"] == []
    assert out["signals"] == []
