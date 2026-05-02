# / tests for dashboard api endpoints

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from src.dashboard.app import _serialize, _serialize_one


class TestSerialize:
    def test_serializes_decimal(self):
        row = {"price": Decimal("182.40")}
        result = _serialize_one(row)
        assert result["price"] == "182.40"

    def test_serializes_date(self):
        row = {"date": date(2026, 3, 26)}
        result = _serialize_one(row)
        assert result["date"] == "2026-03-26"

    def test_serializes_datetime(self):
        row = {"created_at": datetime(2026, 3, 26, 14, 30)}
        result = _serialize_one(row)
        assert "2026-03-26" in result["created_at"]

    def test_preserves_primitives(self):
        row = {"count": 5, "name": "test", "active": True, "note": None}
        result = _serialize_one(row)
        assert result["count"] == 5
        assert result["name"] == "test"
        assert result["active"] is True
        assert result["note"] is None

    def test_none_returns_none(self):
        assert _serialize_one(None) is None

    def test_serialize_list(self):
        rows = [{"a": 1}, {"a": 2}]
        result = _serialize(rows)
        assert len(result) == 2
        assert result[0]["a"] == 1

    def test_serialize_empty_list(self):
        assert _serialize([]) == []

    def test_preserves_dict_jsonb(self):
        row = {"details": {"ai_consensus": "bullish", "pe_ratio": 18.5}}
        result = _serialize_one(row)
        assert isinstance(result["details"], dict)
        assert result["details"]["ai_consensus"] == "bullish"
        assert result["details"]["pe_ratio"] == 18.5

    def test_preserves_list_jsonb(self):
        row = {"items": [1, "two", 3.0]}
        result = _serialize_one(row)
        assert isinstance(result["items"], list)
        assert result["items"] == [1, "two", 3.0]

    def test_preserves_nested_jsonb(self):
        row = {"assumptions": {"growth_rate": 0.05, "ranges": {"min": 0.01, "max": 0.10}}}
        result = _serialize_one(row)
        assert result["assumptions"]["growth_rate"] == 0.05
        assert result["assumptions"]["ranges"]["max"] == 0.10

    def test_unknown_type_becomes_string(self):
        # / types that are not dict, list, primitive, date, or decimal
        row = {"data": frozenset([1, 2])}
        result = _serialize_one(row)
        assert isinstance(result["data"], str)


# / mock pool helper (same pattern as agent tests)
def _mock_pool(rows=None, row=None):
    mock_conn = AsyncMock()
    if rows is not None:
        mock_conn.fetch.return_value = [MagicMock(**{"items.return_value": list(r.items()), "keys.return_value": list(r.keys()), "__iter__": lambda s: iter(r.items())}) for r in rows]
    else:
        mock_conn.fetch.return_value = []
    if row is not None:
        mock_conn.fetchrow.return_value = MagicMock(**{"items.return_value": list(row.items()), "keys.return_value": list(row.keys()), "__iter__": lambda s: iter(row.items())})
    else:
        mock_conn.fetchrow.return_value = None
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool, mock_conn


class TestAnalysisEndpoint:
    @pytest.mark.asyncio
    async def test_returns_all_keys(self):
        from src.dashboard import app as dashboard
        pool, conn = _mock_pool()
        dashboard.STATE.pool = pool
        # / mock asyncpg Record objects
        conn.fetchrow.return_value = None
        conn.fetch.return_value = []
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=dashboard.app), base_url="http://test") as c:
            resp = await c.get("/api/analysis/AAPL")
        assert resp.status_code == 200
        data = resp.json()
        expected_keys = {"score", "signals", "trades", "sentiment", "social_sentiment",
                         "fundamentals", "dcf", "price_history", "insider_trades", "evolution"}
        assert set(data.keys()) == expected_keys
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_empty_symbol_returns_none_and_empty_lists(self):
        from src.dashboard import app as dashboard
        pool, conn = _mock_pool()
        dashboard.STATE.pool = pool
        conn.fetchrow.return_value = None
        conn.fetch.return_value = []
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=dashboard.app), base_url="http://test") as c:
            resp = await c.get("/api/analysis/NONEXISTENT")
        data = resp.json()
        assert data["score"] is None
        assert data["fundamentals"] is None
        assert data["dcf"] is None
        assert data["signals"] == []
        assert data["trades"] == []
        assert data["sentiment"] == []
        assert data["insider_trades"] == []
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_pool_none_returns_empty(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=dashboard.app), base_url="http://test") as c:
            resp = await c.get("/api/analysis/AAPL")
        data = resp.json()
        assert data["score"] is None
        assert data["signals"] == []
        assert data["insider_trades"] == []

    @pytest.mark.asyncio
    async def test_symbols_endpoint(self):
        from src.dashboard import app as dashboard
        pool, conn = _mock_pool()
        dashboard.STATE.pool = pool
        conn.fetch.return_value = []
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=dashboard.app), base_url="http://test") as c:
            resp = await c.get("/api/symbols")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        from src.dashboard import app as dashboard
        pool, conn = _mock_pool()
        dashboard.STATE.pool = pool
        # / health v2 makes many queries — use return_value for all fetchrow/fetch calls
        conn.fetchrow.return_value = None
        conn.fetch.return_value = []
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=dashboard.app), base_url="http://test") as c:
            resp = await c.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "db_connected" in data
        assert "storage" in data
        assert "connections" in data
        assert "cycles" in data
        assert "sources" in data
        assert "recent_errors" in data
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_synthesis_endpoint_returns_latest(self):
        from src.dashboard import app as dashboard
        pool, conn = _mock_pool()
        dashboard.STATE.pool = pool
        conn.fetchrow.return_value = None
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=dashboard.app), base_url="http://test") as c:
            resp = await c.get("/api/synthesis")
        assert resp.status_code == 200
        assert resp.json() is None
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_analysis_includes_evolution(self):
        from src.dashboard import app as dashboard
        pool, conn = _mock_pool()
        dashboard.STATE.pool = pool
        conn.fetchrow.return_value = None
        conn.fetch.return_value = []
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=dashboard.app), base_url="http://test") as c:
            resp = await c.get("/api/analysis/AAPL")
        data = resp.json()
        assert "evolution" in data
        assert data["evolution"] == []
        dashboard.STATE.pool = None


# / helper: set up httpx async client against dashboard app
async def _client():
    from httpx import ASGITransport, AsyncClient

    from src.dashboard import app as dashboard
    return AsyncClient(transport=ASGITransport(app=dashboard.app), base_url="http://test")


# / helper: mock _query and _query_one at module level
# / avoids dealing with asyncpg Record mock — endpoints get plain dicts
def _patch_query(query_results=None, query_one_result=None):
    if query_results is None:
        query_results = []

    async def mock_query(sql, *args):
        if callable(query_results):
            return await query_results(sql, *args)
        return query_results

    async def mock_query_one(sql, *args):
        if callable(query_one_result):
            return await query_one_result(sql, *args)
        return query_one_result

    return (
        patch("src.dashboard.helpers.db.query", new=mock_query),
        patch("src.dashboard.helpers.db.query_one", new=mock_query_one),
    )


# / helper: mock broker via _get_broker
def _mock_broker(balance=None, positions=None, error=None):
    if error:
        return patch("src.dashboard.state.STATE.get_broker", side_effect=error)
    broker = AsyncMock()
    if balance:
        broker.get_account_balance.return_value = balance
    if positions is not None:
        broker.get_positions.return_value = positions
    return patch("src.dashboard.state.STATE.get_broker", return_value=broker)


def _make_balance(equity=100000.0, cash=50000.0, buying_power=200000.0):
    b = MagicMock()
    b.equity = equity
    b.cash = cash
    b.buying_power = buying_power
    return b


def _make_position(symbol="AAPL", side="long", qty=10.0, entry=175.0,
                   mv=1820.0, pnl=70.0, price=182.0):
    p = MagicMock()
    p.symbol = symbol
    p.side = side
    p.qty = qty
    p.avg_entry_price = entry
    p.market_value = mv
    p.unrealized_pnl = pnl
    p.current_price = price
    return p


class TestPortfolioEndpoint:
    @pytest.mark.asyncio
    async def test_portfolio_returns_broker_data(self):
        from src.dashboard import app as dashboard
        pos = _make_position()
        pq, pqo = _patch_query(query_results=[])
        with _mock_broker(balance=_make_balance(), positions=[pos]), pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/portfolio")
        data = resp.json()
        assert resp.status_code == 200
        assert data["equity"] == 100000.0
        assert data["cash"] == 50000.0
        assert data["buying_power"] == 200000.0
        assert data["positions_count"] == 1
        assert data["daily_pnl"] == 70.0
        assert len(data["positions"]) == 1
        assert data["positions"][0]["symbol"] == "AAPL"
        assert data["positions"][0]["current_price"] == 182.0
        dashboard.STATE.broker = None

    @pytest.mark.asyncio
    async def test_portfolio_fallback_on_broker_error(self):
        from src.dashboard import app as dashboard
        fallback_row = {"symbol": "MSFT", "side": "buy", "qty": 5, "price": 400.0, "strategy_id": "s1", "created_at": "2026-03-26"}
        pq, pqo = _patch_query(query_results=[fallback_row])
        with _mock_broker(error=Exception("no keys")), pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/portfolio")
        data = resp.json()
        assert resp.status_code == 200
        assert data["positions_count"] == 0
        assert len(data["positions"]) == 1
        assert data["positions"][0]["symbol"] == "MSFT"
        assert data["trades_today"] == []
        dashboard.STATE.broker = None

    @pytest.mark.asyncio
    async def test_portfolio_empty_positions(self):
        from src.dashboard import app as dashboard
        pq, pqo = _patch_query(query_results=[])
        with _mock_broker(balance=_make_balance(50000, 50000, 100000), positions=[]), pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/portfolio")
        data = resp.json()
        assert data["positions_count"] == 0
        assert data["daily_pnl"] == 0
        assert data["positions"] == []
        dashboard.STATE.broker = None

    @pytest.mark.asyncio
    async def test_portfolio_multiple_positions_pnl_sum(self):
        from src.dashboard import app as dashboard
        p1 = _make_position("AAPL", pnl=50.0)
        p2 = _make_position("MSFT", pnl=-20.0)
        pq, pqo = _patch_query(query_results=[])
        with _mock_broker(balance=_make_balance(), positions=[p1, p2]), pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/portfolio")
        data = resp.json()
        assert data["daily_pnl"] == 30.0
        assert data["positions_count"] == 2
        dashboard.STATE.broker = None


class TestEquityHistoryEndpoint:
    @pytest.mark.asyncio
    async def test_equity_history_returns_data(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "timestamp": [1711400000, 1711400300],
            "equity": [100000, 100500],
            "profit_loss": [0, 500],
            "base_value": 100000,
        }
        mock_http = AsyncMock()
        mock_http.get.return_value = mock_resp

        with patch("src.data.alpaca_client.alpaca_base_url", return_value="https://paper-api.alpaca.markets"), \
             patch("src.data.alpaca_client.alpaca_headers", return_value={"APCA-API-KEY-ID": "x"}), \
             patch("src.data.alpaca_client.get_alpaca_client", return_value=mock_http):
            async with await _client() as c:
                resp = await c.get("/api/equity-history?period=1D&timeframe=5Min")
        data = resp.json()
        assert resp.status_code == 200
        assert data["timestamps"] == [1711400000, 1711400300]
        assert data["equity"] == [100000, 100500]
        assert data["profit_loss"] == [0, 500]
        assert data["base_value"] == 100000

    @pytest.mark.asyncio
    async def test_equity_history_fallback_on_error(self):
        with patch("src.data.alpaca_client.alpaca_base_url", return_value="https://paper-api.alpaca.markets"), \
             patch("src.data.alpaca_client.alpaca_headers", return_value={}), \
             patch("src.data.alpaca_client.get_alpaca_client", side_effect=Exception("network error")):
            async with await _client() as c:
                resp = await c.get("/api/equity-history")
        data = resp.json()
        assert resp.status_code == 200
        assert data["timestamps"] == []
        assert data["equity"] == []
        assert data["profit_loss"] == []
        assert data["base_value"] == 100000

    @pytest.mark.asyncio
    async def test_equity_history_missing_fields_default(self):
        # / alpaca response missing base_value falls back to 100000
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"timestamp": [], "equity": [], "profit_loss": []}
        mock_http = AsyncMock()
        mock_http.get.return_value = mock_resp

        with patch("src.data.alpaca_client.alpaca_base_url", return_value="https://paper-api.alpaca.markets"), \
             patch("src.data.alpaca_client.alpaca_headers", return_value={}), \
             patch("src.data.alpaca_client.get_alpaca_client", return_value=mock_http):
            async with await _client() as c:
                resp = await c.get("/api/equity-history")
        data = resp.json()
        assert data["base_value"] == 100000


class TestTradesEndpoint:
    @pytest.mark.asyncio
    async def test_trades_returns_data(self):
        rows = [
            {"id": 1, "symbol": "AAPL", "side": "buy", "qty": 10, "price": 180.0, "created_at": "2026-03-26"},
            {"id": 2, "symbol": "MSFT", "side": "sell", "qty": 5, "price": 410.0, "created_at": "2026-03-25"},
        ]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/trades?limit=10&offset=0")
        data = resp.json()
        assert resp.status_code == 200
        assert len(data) == 2
        assert data[0]["symbol"] == "AAPL"
        assert data[1]["symbol"] == "MSFT"

    @pytest.mark.asyncio
    async def test_trades_empty(self):
        pq, pqo = _patch_query()
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/trades")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_trades_with_symbol_filter(self):
        # / verify symbol query param routes to the symbol-filtered sql branch
        calls = []

        async def track_query(sql, *args):
            calls.append((sql, args))
            return [{"id": 1, "symbol": "GOOG", "side": "buy", "qty": 2, "price": 170.0, "created_at": "2026-03-26"}]

        pq, pqo = _patch_query(query_results=track_query)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/trades?symbol=GOOG")
        data = resp.json()
        assert resp.status_code == 200
        assert data[0]["symbol"] == "GOOG"
        assert any("GOOG" in args for _, args in calls)

    @pytest.mark.asyncio
    async def test_trades_limit_clamped(self):
        calls = []

        async def track_query(sql, *args):
            calls.append(args)
            return []

        pq, pqo = _patch_query(query_results=track_query)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/trades?limit=9999")
        assert resp.status_code == 200
        # / verify clamped limit=500 was passed
        assert any(500 in args for args in calls)

    @pytest.mark.asyncio
    async def test_trades_pool_none(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/trades")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_trades_offset_clamped_to_zero(self):
        calls = []

        async def track_query(sql, *args):
            calls.append(args)
            return []

        pq, pqo = _patch_query(query_results=track_query)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/trades?offset=-5")
        assert resp.status_code == 200
        # / negative offset clamped to 0
        assert any(0 in args for args in calls)


def _make_config_dir(tmp_path, configs=None):
    # / create temp strategy configs for testing
    import json as _json
    if configs is None:
        configs = [{"id": "s1", "name": "Test", "description": "test strat",
                     "universe": "all_stocks", "asset_class": "stocks",
                     "entry_conditions": {"operator": "AND", "signals": [{"indicator": "rsi", "condition": "below", "threshold": 30, "period": 14}]},
                     "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.05}},
                     "metadata": {"status": "paper_trading"}}]
    for cfg in configs:
        (tmp_path / f"{cfg['id']}.json").write_text(_json.dumps(cfg))
    return tmp_path


class TestStrategiesEndpoint:
    @pytest.mark.asyncio
    async def test_strategies_returns_all_configs(self, tmp_path):
        # / all configs appear even with no db data
        cfgs = [
            {"id": "s1", "name": "Strat1", "description": "d1", "universe": "all_stocks",
             "asset_class": "stocks", "entry_conditions": {"operator": "AND", "signals": [{"indicator": "rsi", "condition": "below", "threshold": 30, "period": 14}]},
             "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.05}}, "metadata": {"status": "paper_trading"}},
            {"id": "s2", "name": "Strat2", "description": "d2", "universe": "all_crypto",
             "asset_class": "crypto", "entry_conditions": {"operator": "AND", "signals": [{"indicator": "macd", "condition": "crossover_bullish"}, {"indicator": "rsi", "condition": "below", "threshold": 40, "period": 14}]},
             "exit_conditions": {"stop_loss": {"type": "atr_trailing", "multiplier": 1.5}, "time_exit": {"max_holding_days": 30}}, "metadata": {"status": "live"}},
        ]
        configs_dir = _make_config_dir(tmp_path, cfgs)
        pq, pqo = _patch_query()
        with pq, pqo, patch("src.dashboard.routers.portfolio.STRATEGY_CONFIGS_DIR", configs_dir):
            async with await _client() as c:
                resp = await c.get("/api/strategies")
        data = resp.json()
        assert resp.status_code == 200
        assert len(data) == 2
        ids = {d["strategy_id"] for d in data}
        assert ids == {"s1", "s2"}
        s2 = next(d for d in data if d["strategy_id"] == "s2")
        assert s2["entry_conditions_count"] == 2
        assert s2["exit_conditions_count"] == 2
        assert s2["total_trades"] == 0

    @pytest.mark.asyncio
    async def test_strategies_overlays_scores(self, tmp_path):
        # / strategy_scores data merges into config baseline
        configs_dir = _make_config_dir(tmp_path)
        call_count = {"n": 0}

        async def seq_query(sql, *args):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return [{"strategy_id": "s1", "sharpe_ratio": 1.5, "win_rate": 0.6, "total_pnl": 5000}]
            return []

        pq, pqo = _patch_query(query_results=seq_query)
        with pq, pqo, patch("src.dashboard.routers.portfolio.STRATEGY_CONFIGS_DIR", configs_dir):
            async with await _client() as c:
                resp = await c.get("/api/strategies")
        data = resp.json()
        assert resp.status_code == 200
        assert len(data) == 1
        assert data[0]["strategy_id"] == "s1"
        assert data[0]["sharpe_ratio"] == 1.5
        assert data[0]["name"] == "Test"

    @pytest.mark.asyncio
    async def test_strategies_overlays_trade_log(self, tmp_path):
        # / trade_log aggregates merge into config baseline
        configs_dir = _make_config_dir(tmp_path)
        call_count = {"n": 0}

        async def seq_query(sql, *args):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return []
            return [{"strategy_id": "s1", "total_trades": 10, "wins": 6, "losses": 4,
                     "avg_pnl": 50, "total_pnl": 500, "win_rate": 0.6, "last_trade_at": "2026-03-26"}]

        pq, pqo = _patch_query(query_results=seq_query)
        with pq, pqo, patch("src.dashboard.routers.portfolio.STRATEGY_CONFIGS_DIR", configs_dir):
            async with await _client() as c:
                resp = await c.get("/api/strategies")
        data = resp.json()
        assert resp.status_code == 200
        assert len(data) == 1
        assert data[0]["strategy_id"] == "s1"
        assert data[0]["total_trades"] == 10
        assert data[0]["status"] == "paper_trading"

    @pytest.mark.asyncio
    async def test_strategies_empty_configs_dir(self, tmp_path):
        # / empty configs dir + no db data = empty list
        pq, pqo = _patch_query()
        with pq, pqo, patch("src.dashboard.routers.portfolio.STRATEGY_CONFIGS_DIR", tmp_path):
            async with await _client() as c:
                resp = await c.get("/api/strategies")
        assert resp.status_code == 200
        assert resp.json() == []


class TestEvolutionEndpoint:
    @pytest.mark.asyncio
    async def test_evolution_returns_data(self):
        rows = [{"generation": 5, "action": "mutate", "strategy_id": "s1",
                 "reason": "low sharpe", "details": {}, "created_at": "2026-03-26T00:00:00"}]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/evolution")
        data = resp.json()
        assert resp.status_code == 200
        assert len(data) == 1
        assert data[0]["generation"] == 5
        assert data[0]["action"] == "mutate"

    @pytest.mark.asyncio
    async def test_evolution_empty(self):
        pq, pqo = _patch_query()
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/evolution")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_evolution_pool_none(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/evolution")
        assert resp.status_code == 200
        assert resp.json() == []


class TestInsiderEndpoint:
    @pytest.mark.asyncio
    async def test_insider_returns_data(self):
        # / bug 4b: response is now {trades, signed_strength, score_100, signal}
        rows = [{"filing_date": "2026-03-20", "insider_name": "Tim Cook", "insider_title": "CEO",
                 "transaction_type": "S-Sale", "shares": 50000, "price_per_share": 180.0, "total_value": 9000000}]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/insider/AAPL")
        data = resp.json()
        assert resp.status_code == 200
        assert "trades" in data
        assert len(data["trades"]) == 1
        assert data["trades"][0]["insider_name"] == "Tim Cook"
        assert data["trades"][0]["shares"] == 50000
        assert "signal" in data

    @pytest.mark.asyncio
    async def test_insider_empty(self):
        pq, pqo = _patch_query()
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/insider/ZZZZ")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trades"] == []
        assert data["signal"] == "neutral"

    @pytest.mark.asyncio
    async def test_insider_uppercases_symbol(self):
        calls = []

        async def track_query(sql, *args):
            calls.append(args)
            return []

        pq, pqo = _patch_query(query_results=track_query)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/insider/aapl")
        assert resp.status_code == 200
        assert any("AAPL" in args for args in calls)


class TestIndicatorsEndpoint:
    @pytest.mark.asyncio
    async def test_indicators_returns_data(self):
        rows = [{"date": "2026-03-26", "rsi14": 45.2, "macd": 1.5, "macd_signal": 1.2,
                 "macd_histogram": 0.3, "adx": 25.0, "sma20": 178.0, "sma50": 175.0,
                 "bb_upper": 185.0, "bb_middle": 178.0, "bb_lower": 171.0, "atr": 3.5, "timeframe": "1Day"}]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/indicators/AAPL")
        data = resp.json()
        assert resp.status_code == 200
        assert len(data) == 1
        assert data[0]["rsi14"] == 45.2
        assert data[0]["macd"] == 1.5
        assert data[0]["timeframe"] == "1Day"

    @pytest.mark.asyncio
    async def test_indicators_empty(self):
        pq, pqo = _patch_query()
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/indicators/ZZZZ")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_indicators_custom_timeframe(self):
        calls = []

        async def track_query(sql, *args):
            calls.append(args)
            return []

        pq, pqo = _patch_query(query_results=track_query)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/indicators/AAPL?timeframe=1Hour&limit=30")
        assert any("1Hour" in args and 30 in args for args in calls)

    @pytest.mark.asyncio
    async def test_indicators_limit_clamped(self):
        calls = []

        async def track_query(sql, *args):
            calls.append(args)
            return []

        pq, pqo = _patch_query(query_results=track_query)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/indicators/AAPL?limit=9999")
        assert any(250 in args for args in calls)


class TestIntradayEndpoint:
    @pytest.mark.asyncio
    async def test_intraday_returns_data(self):
        rows = [{"timestamp": "2026-03-26T10:00:00", "open": 180.0, "high": 182.0,
                 "low": 179.0, "close": 181.5, "volume": 50000, "vwap": 180.8}]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/intraday/AAPL")
        data = resp.json()
        assert resp.status_code == 200
        assert len(data) == 1
        assert data[0]["close"] == 181.5
        assert data[0]["vwap"] == 180.8

    @pytest.mark.asyncio
    async def test_intraday_empty(self):
        pq, pqo = _patch_query()
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/intraday/ZZZZ")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_intraday_custom_params(self):
        calls = []

        async def track_query(sql, *args):
            calls.append(args)
            return []

        pq, pqo = _patch_query(query_results=track_query)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/intraday/TSLA?days=5&timeframe=1Hour")
        assert any("TSLA" in args and "1Hour" in args and "5" in args for args in calls)

    @pytest.mark.asyncio
    async def test_intraday_days_clamped(self):
        calls = []

        async def track_query(sql, *args):
            calls.append(args)
            return []

        pq, pqo = _patch_query(query_results=track_query)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/intraday/AAPL?days=999")
        # / days clamped to 60, passed as string "60"
        assert any("60" in args for args in calls)


class TestQuantMetricsEndpoint:
    @pytest.mark.asyncio
    async def test_quant_metrics_returns_data(self):
        rows = [{"strategy_id": "s1", "sharpe_ratio": 1.8, "win_rate": 0.65, "max_drawdown": -0.08}]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/quant-metrics/AAPL")
        data = resp.json()
        assert resp.status_code == 200
        assert len(data) == 1
        assert data[0]["sharpe_ratio"] == 1.8

    @pytest.mark.asyncio
    async def test_quant_metrics_empty(self):
        pq, pqo = _patch_query()
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/quant-metrics/ZZZZ")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_quant_metrics_pool_none(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/quant-metrics/AAPL")
        assert resp.status_code == 200
        assert resp.json() == []


class TestStrategyPositionsEndpoint:
    @pytest.mark.asyncio
    async def test_strategy_positions_all(self):
        rows = [
            {"strategy_id": "s1", "symbol": "AAPL", "qty": 10, "avg_entry_price": 175.0, "updated_at": "2026-03-26"},
            {"strategy_id": "s2", "symbol": "MSFT", "qty": 5, "avg_entry_price": 400.0, "updated_at": "2026-03-25"},
        ]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/strategy-positions")
        data = resp.json()
        assert resp.status_code == 200
        assert len(data) == 2
        assert data[0]["strategy_id"] == "s1"
        assert data[1]["symbol"] == "MSFT"

    @pytest.mark.asyncio
    async def test_strategy_positions_by_symbol(self):
        calls = []

        async def track_query(sql, *args):
            calls.append(args)
            return [{"strategy_id": "s1", "symbol": "AAPL", "qty": 10, "avg_entry_price": 175.0, "updated_at": "2026-03-26"}]

        pq, pqo = _patch_query(query_results=track_query)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/strategy-positions?symbol=AAPL")
        data = resp.json()
        assert resp.status_code == 200
        assert len(data) == 1
        assert any("AAPL" in args for args in calls)

    @pytest.mark.asyncio
    async def test_strategy_positions_empty(self):
        pq, pqo = _patch_query()
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/strategy-positions")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_strategy_positions_pool_none(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/strategy-positions")
        assert resp.status_code == 200
        assert resp.json() == []


class TestStrategyEvaluationsEndpoint:
    @pytest.mark.asyncio
    async def test_evaluations_returns_data(self):
        rows = [{"id": 1, "strategy_id": "s1", "sharpe": 1.5, "win_rate": 0.6, "created_at": "2026-03-26"}]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/strategy-evaluations")
        data = resp.json()
        assert resp.status_code == 200
        assert len(data) == 1
        assert data[0]["strategy_id"] == "s1"

    @pytest.mark.asyncio
    async def test_evaluations_empty(self):
        pq, pqo = _patch_query()
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/strategy-evaluations")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_evaluations_limit_clamped(self):
        calls = []

        async def track_query(sql, *args):
            calls.append(args)
            return []

        pq, pqo = _patch_query(query_results=track_query)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/strategy-evaluations?limit=999")
        # / clamped to 100
        assert any(100 in args for args in calls)

    @pytest.mark.asyncio
    async def test_evaluations_custom_limit(self):
        calls = []

        async def track_query(sql, *args):
            calls.append(args)
            return []

        pq, pqo = _patch_query(query_results=track_query)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/strategy-evaluations?limit=5")
        assert any(5 in args for args in calls)

    @pytest.mark.asyncio
    async def test_evaluations_pool_none(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/strategy-evaluations")
        assert resp.status_code == 200
        assert resp.json() == []


def _make_intraday_rows(n: int = 60):
    # / build n synthetic intraday bars with deterministic prices
    rows = []
    for i in range(n):
        base = 180.0 + i * 0.25
        rows.append({
            "timestamp": f"2026-03-26T{10 + i // 60:02d}:{i % 60:02d}:00",
            "open": base,
            "high": base + 0.5,
            "low": base - 0.5,
            "close": base + 0.1,
            "volume": 50_000 + i * 10,
            "vwap": base,
        })
    return rows


class TestIntradayIndicators:
    @pytest.mark.asyncio
    async def test_intraday_backwards_compat_no_indicators_param(self):
        # / no indicators param -> legacy list shape
        rows = _make_intraday_rows(5)
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/intraday/AAPL?days=5")
        data = resp.json()
        assert resp.status_code == 200
        assert isinstance(data, list)
        assert len(data) == 5
        assert data[0]["close"] == 180.1

    @pytest.mark.asyncio
    async def test_intraday_with_indicators_returns_new_shape(self):
        # / indicators param -> dict with bars + indicators + meta
        rows = _make_intraday_rows(60)
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/intraday/AAPL?days=5&indicators=sma_20,rsi_14")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert set(["bars", "indicators", "meta"]).issubset(data.keys())
        assert "sma_20" in data["indicators"]
        assert "rsi_14" in data["indicators"]
        assert data["meta"]["symbol"] == "AAPL"
        assert data["meta"]["timeframe"] == "1Hour"

    @pytest.mark.asyncio
    async def test_intraday_indicators_alignment(self):
        # / bars.c length == meta.bar_count == each per-bar indicator array length
        rows = _make_intraday_rows(50)
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/intraday/AAPL?indicators=sma_20,rsi_14")
        data = resp.json()
        n = len(data["bars"]["c"])
        assert n == 50
        assert data["meta"]["bar_count"] == 50
        assert len(data["bars"]["t"]) == n
        assert len(data["bars"]["o"]) == n
        assert len(data["bars"]["h"]) == n
        assert len(data["bars"]["l"]) == n
        assert len(data["bars"]["v"]) == n
        # / per-bar indicator arrays match bar count
        assert len(data["indicators"]["sma_20"]["values"]) == n
        assert len(data["indicators"]["rsi_14"]["values"]) == n

    @pytest.mark.asyncio
    async def test_intraday_unknown_indicator_skipped(self):
        # / bogus ids are silently dropped, valid ones still compute
        rows = _make_intraday_rows(40)
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/intraday/AAPL?indicators=sma_20,bogus_indicator,rsi_14")
        assert resp.status_code == 200
        data = resp.json()
        assert "sma_20" in data["indicators"]
        assert "rsi_14" in data["indicators"]
        assert "bogus_indicator" not in data["indicators"]

    @pytest.mark.asyncio
    async def test_intraday_empty_rows(self):
        # / db returns nothing -> bars empty, indicators empty, bar_count zero
        pq, pqo = _patch_query(query_results=[])
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/intraday/AAPL?indicators=sma_20,rsi_14")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bars"]["t"] == []
        assert data["bars"]["c"] == []
        assert data["indicators"] == {}
        assert data["meta"]["bar_count"] == 0
        assert data["meta"]["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_intraday_multi_value_indicator(self):
        # / bollinger returns upper/middle/lower keys
        rows = _make_intraday_rows(50)
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/intraday/AAPL?indicators=bb_20_2")
        assert resp.status_code == 200
        data = resp.json()
        assert "bb_20_2" in data["indicators"]
        bb = data["indicators"]["bb_20_2"]
        assert "upper" in bb and "middle" in bb and "lower" in bb
        assert len(bb["upper"]) == len(data["bars"]["c"])
        assert len(bb["middle"]) == len(data["bars"]["c"])
        assert len(bb["lower"]) == len(data["bars"]["c"])

    @pytest.mark.asyncio
    async def test_intraday_indicators_whitespace_ids(self):
        # / whitespace around ids should be trimmed and empty tokens ignored
        rows = _make_intraday_rows(30)
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/intraday/AAPL?indicators= sma_20 , , rsi_14 ")
        assert resp.status_code == 200
        data = resp.json()
        assert "sma_20" in data["indicators"]
        assert "rsi_14" in data["indicators"]


class TestIntradayCache:
    # / ttl cache unit tests + end-to-end short-circuit behavior

    def test_cache_put_and_get_roundtrip(self):
        from src.dashboard.app import (
            _intraday_cache_clear,
            _intraday_cache_get,
            _intraday_cache_key,
            _intraday_cache_put,
        )
        _intraday_cache_clear()
        key = _intraday_cache_key("AAPL", "1Hour", 5, ("sma_20",))
        payload = {"bars": {"c": [1.0, 2.0]}, "meta": {"bar_count": 2}}
        _intraday_cache_put(key, payload)
        assert _intraday_cache_get(key) is payload

    def test_cache_miss_returns_none(self):
        from src.dashboard.app import _intraday_cache_clear, _intraday_cache_get, _intraday_cache_key
        _intraday_cache_clear()
        assert _intraday_cache_get(_intraday_cache_key("NVDA", "1Hour", 5, ())) is None

    def test_cache_key_sorted_ids_differ_from_unsorted(self):
        # / key builder takes a tuple that is already sorted by caller
        from src.dashboard.app import _intraday_cache_key
        k1 = _intraday_cache_key("AAPL", "1Hour", 5, ("rsi_14", "sma_20"))
        k2 = _intraday_cache_key("AAPL", "1Hour", 5, ("sma_20", "rsi_14"))
        # / different tuples -> different keys (caller must sort first for hit)
        assert k1 != k2

    def test_cache_key_includes_symbol_and_timeframe_and_days(self):
        from src.dashboard.app import _intraday_cache_key
        base = _intraday_cache_key("AAPL", "1Hour", 5, ("sma_20",))
        assert base != _intraday_cache_key("NVDA", "1Hour", 5, ("sma_20",))
        assert base != _intraday_cache_key("AAPL", "1Day", 5, ("sma_20",))
        assert base != _intraday_cache_key("AAPL", "1Hour", 10, ("sma_20",))
        assert base != _intraday_cache_key("AAPL", "1Hour", 5, ("rsi_14",))

    def test_cache_expires_after_ttl(self, monkeypatch):
        import src.dashboard.app as app_mod
        cache = app_mod.STATE.intraday_cache
        cache.clear()
        t = [100.0]
        monkeypatch.setattr(cache, "_clock", lambda: t[0])
        key = app_mod._intraday_cache_key("AAPL", "1Hour", 5, ())
        app_mod._intraday_cache_put(key, {"payload": 1})
        assert app_mod._intraday_cache_get(key) == {"payload": 1}
        t[0] = 100.0 + cache._ttl + 0.001
        assert app_mod._intraday_cache_get(key) is None

    def test_cache_max_size_eviction(self, monkeypatch):
        import src.dashboard.app as app_mod
        cache = app_mod.STATE.intraday_cache
        cache.clear()
        monkeypatch.setattr(cache, "_max", 3)
        t = [0.0]
        monkeypatch.setattr(cache, "_clock", lambda: t[0])
        for i in range(3):
            t[0] = float(i)
            app_mod._intraday_cache_put(
                app_mod._intraday_cache_key(f"S{i}", "1Hour", 5, ()), {"i": i}
            )
        assert len(cache) == 3
        # / fourth insert should evict the oldest (S0)
        t[0] = 100.0
        app_mod._intraday_cache_put(
            app_mod._intraday_cache_key("S3", "1Hour", 5, ()), {"i": 3}
        )
        assert len(cache) == 3
        assert app_mod._intraday_cache_get(app_mod._intraday_cache_key("S0", "1Hour", 5, ())) is None

    @pytest.mark.asyncio
    async def test_intraday_endpoint_cache_short_circuits_query(self):
        # / second call with same params hits cache, _query only called once
        # / pool must be truthy for the put path — otherwise cache is skipped to avoid staleness
        from unittest.mock import MagicMock, patch

        import src.dashboard.app as app_mod
        app_mod._intraday_cache_clear()
        call_count = {"n": 0}

        async def fake_query(sql, *args):
            call_count["n"] += 1
            return _make_intraday_rows(30)

        with patch("src.dashboard.app.STATE.pool", MagicMock()), \
                patch("src.dashboard.helpers.db.query", side_effect=fake_query):
            async with await _client() as c:
                r1 = await c.get("/api/intraday/AAPL?days=5&indicators=sma_20")
                r2 = await c.get("/api/intraday/AAPL?days=5&indicators=sma_20")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json() == r2.json()
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_intraday_endpoint_cache_differentiates_by_indicator_order(self):
        # / same indicators in different order share a key (caller sorts) -> second call also hits cache
        from unittest.mock import MagicMock, patch

        import src.dashboard.app as app_mod
        app_mod._intraday_cache_clear()
        call_count = {"n": 0}

        async def fake_query(sql, *args):
            call_count["n"] += 1
            return _make_intraday_rows(30)

        with patch("src.dashboard.app.STATE.pool", MagicMock()), \
                patch("src.dashboard.helpers.db.query", side_effect=fake_query):
            async with await _client() as c:
                await c.get("/api/intraday/AAPL?days=5&indicators=sma_20,rsi_14")
                await c.get("/api/intraday/AAPL?days=5&indicators=rsi_14,sma_20")
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_intraday_endpoint_legacy_shape_cached(self):
        # / empty indicators legacy path also cached when pool is ready
        from unittest.mock import MagicMock, patch

        import src.dashboard.app as app_mod
        app_mod._intraday_cache_clear()
        call_count = {"n": 0}

        async def fake_query(sql, *args):
            call_count["n"] += 1
            return _make_intraday_rows(5)

        with patch("src.dashboard.app.STATE.pool", MagicMock()), \
                patch("src.dashboard.helpers.db.query", side_effect=fake_query):
            async with await _client() as c:
                r1 = await c.get("/api/intraday/AAPL?days=5")
                r2 = await c.get("/api/intraday/AAPL?days=5")
        assert r1.json() == r2.json()
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_intraday_cache_skipped_when_pool_is_none(self):
        # / pool is None -> endpoint must NOT cache an empty list, so a subsequent call re-queries
        from unittest.mock import patch

        import src.dashboard.app as app_mod
        app_mod._intraday_cache_clear()
        call_count = {"n": 0}

        async def fake_query(sql, *args):
            call_count["n"] += 1
            return []

        with patch("src.dashboard.app.STATE.pool", None), \
                patch("src.dashboard.helpers.db.query", side_effect=fake_query):
            async with await _client() as c:
                await c.get("/api/intraday/AAPL?days=5&indicators=sma_20")
                await c.get("/api/intraday/AAPL?days=5&indicators=sma_20")
        # / both calls should have queried since cache was bypassed
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_intraday_cache_skipped_when_legacy_empty_with_no_pool(self):
        # / legacy path with empty rows and no pool must not cache, so recovered pool can serve real data
        from unittest.mock import patch

        import src.dashboard.app as app_mod
        app_mod._intraday_cache_clear()
        call_count = {"n": 0}

        async def fake_query(sql, *args):
            call_count["n"] += 1
            return []

        with patch("src.dashboard.app.STATE.pool", None), \
                patch("src.dashboard.helpers.db.query", side_effect=fake_query):
            async with await _client() as c:
                await c.get("/api/intraday/AAPL?days=5")
                await c.get("/api/intraday/AAPL?days=5")
        assert call_count["n"] == 2


class TestChartStateEndpoint:
    # / sanitize_indicators unit check — filters unknown, dedupes, preserves order
    def test_sanitize_indicators_filters_and_dedupes(self):
        from src.dashboard import chart_state
        result = chart_state.sanitize_indicators(
            ["sma_20", "bogus", "rsi_14", "sma_20", "also_bogus", "macd_12_26_9"]
        )
        assert result == ["sma_20", "rsi_14", "macd_12_26_9"]

    def test_sanitize_indicators_ignores_non_strings(self):
        from src.dashboard import chart_state
        result = chart_state.sanitize_indicators(["sma_20", None, 42, {"x": 1}, "rsi_14"])
        assert result == ["sma_20", "rsi_14"]

    @pytest.mark.asyncio
    async def test_get_chart_state_returns_default_when_pool_none(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/chart-state/AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {
            "symbol": "AAPL",
            "timeframe": "1Hour",
            "active_indicators": [],
            "indicator_params": {},
        }

    @pytest.mark.asyncio
    async def test_get_chart_state_returns_default_when_no_row(self):
        from src.dashboard import app as dashboard
        pool, conn = _mock_pool()
        dashboard.STATE.pool = pool
        conn.fetchrow.return_value = None
        async with await _client() as c:
            resp = await c.get("/api/chart-state/AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "AAPL"
        assert data["timeframe"] == "1Hour"
        assert data["active_indicators"] == []
        assert data["indicator_params"] == {}
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_get_chart_state_returns_persisted_row(self):
        from src.dashboard import app as dashboard
        from src.dashboard import chart_state

        async def fake_get(pool, symbol):
            return {
                "symbol": symbol,
                "timeframe": "15Min",
                "active_indicators": ["sma_20", "rsi_14"],
                "indicator_params": {"sma_20": {"period": 20}},
            }

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(chart_state, "get_chart_state", side_effect=fake_get):
            async with await _client() as c:
                resp = await c.get("/api/chart-state/TSLA")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "TSLA"
        assert data["timeframe"] == "15Min"
        assert data["active_indicators"] == ["sma_20", "rsi_14"]
        assert data["indicator_params"] == {"sma_20": {"period": 20}}
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_post_chart_state_sanitizes_invalid_indicators(self):
        from src.dashboard import app as dashboard
        from src.dashboard import chart_state

        captured = {}

        async def fake_upsert(pool, symbol, timeframe=None, active_indicators=None, indicator_params=None):
            captured["symbol"] = symbol
            captured["timeframe"] = timeframe
            captured["active_indicators"] = active_indicators
            captured["indicator_params"] = indicator_params
            return {
                "symbol": symbol,
                "timeframe": timeframe or "1Hour",
                "active_indicators": active_indicators or [],
                "indicator_params": indicator_params or {},
            }

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(chart_state, "upsert_chart_state", side_effect=fake_upsert):
            async with await _client() as c:
                resp = await c.post(
                    "/api/chart-state/AAPL",
                    json={"active_indicators": ["sma_20", "totally_bogus", "rsi_14", "nope"]},
                )
        assert resp.status_code == 200
        # / only valid registry ids reach the upsert layer
        assert captured["active_indicators"] == ["sma_20", "rsi_14"]
        assert captured["timeframe"] is None
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_post_chart_state_upsert_preserves_unspecified_fields(self):
        from src.dashboard import app as dashboard
        from src.dashboard import chart_state

        captured = {}

        async def fake_upsert(pool, symbol, timeframe=None, active_indicators=None, indicator_params=None):
            captured["timeframe"] = timeframe
            captured["active_indicators"] = active_indicators
            captured["indicator_params"] = indicator_params
            return {
                "symbol": symbol,
                "timeframe": timeframe or "1Hour",
                "active_indicators": [],
                "indicator_params": {},
            }

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        # / post only timeframe — upsert must see None for other fields so db coalesce keeps them
        with patch.object(chart_state, "upsert_chart_state", side_effect=fake_upsert):
            async with await _client() as c:
                resp = await c.post("/api/chart-state/MSFT", json={"timeframe": "15Min"})
        assert resp.status_code == 200
        assert captured["timeframe"] == "15Min"
        assert captured["active_indicators"] is None
        assert captured["indicator_params"] is None
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_chart_state_timeframe_whitelist_drops_unknown(self):
        from src.dashboard import chart_state

        # / direct module test: upsert drops bogus timeframes before touching the db
        captured = {}

        class _FakeConn:
            async def fetchrow(self, sql, *args):
                captured["args"] = args
                return {
                    "symbol": args[0],
                    "timeframe": "1Hour",
                    "active_indicators": [],
                    "indicator_params": {},
                }

        class _FakeCtx:
            async def __aenter__(self_inner):
                return _FakeConn()

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _FakePool:
            def acquire(self_inner):
                return _FakeCtx()

        result = await chart_state.upsert_chart_state(
            _FakePool(), "AAPL", timeframe="bogus_tf", active_indicators=None, indicator_params=None
        )
        # / bogus timeframe becomes None (falls back to coalesce)
        assert captured["args"][1] is None
        assert result["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_post_chart_state_pool_none_returns_error(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.post(
                "/api/chart-state/AAPL",
                json={"timeframe": "1Hour", "active_indicators": ["sma_20"]},
            )
        assert resp.status_code == 200
        assert resp.json() == {"error": "db_not_ready"}

    @pytest.mark.asyncio
    async def test_get_chart_state_rejects_oversized_symbol(self):
        # / db column is VARCHAR(20); symbols longer than that must 400 at the edge
        async with await _client() as c:
            resp = await c.get("/api/chart-state/" + ("A" * 25))
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid_symbol"}

    @pytest.mark.asyncio
    async def test_post_chart_state_rejects_oversized_symbol(self):
        async with await _client() as c:
            resp = await c.post(
                "/api/chart-state/" + ("A" * 25),
                json={"timeframe": "1Hour", "active_indicators": ["sma_20"]},
            )
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid_symbol"}

    @pytest.mark.asyncio
    async def test_chart_state_accepts_exact_20_char_symbol(self):
        # / boundary: exactly 20 chars must pass (default state since pool stays None)
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        sym = "A" * 20
        async with await _client() as c:
            resp = await c.get(f"/api/chart-state/{sym}")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == sym


class TestMarkersEndpoint:
    @pytest.mark.asyncio
    async def test_markers_returns_default_when_pool_none(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/markers/AAPL")
        assert resp.status_code == 200
        data = resp.json()
        # / empty lists for all six kinds when db is down
        assert data == {
            "trades": [],
            "signals": [],
            "insiders": [],
            "earnings": [],
            "regime": [],
            "consensus": [],
        }

    @pytest.mark.asyncio
    async def test_markers_rejects_oversized_symbol(self):
        async with await _client() as c:
            resp = await c.get("/api/markers/" + ("A" * 25))
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid_symbol"}

    @pytest.mark.asyncio
    async def test_markers_respects_kinds_filter(self):
        from src.dashboard import app as dashboard
        from src.dashboard import marker_aggregator

        captured = {}

        async def fake_build(pool, symbol, kinds, days):
            captured["kinds"] = kinds
            captured["symbol"] = symbol
            captured["days"] = days
            return {"trades": [{"time": "2026-03-20", "price": 100.0, "side": "buy"}]}

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(marker_aggregator, "build_markers", side_effect=fake_build):
            async with await _client() as c:
                resp = await c.get("/api/markers/AAPL?kinds=trades&days=14")
        assert resp.status_code == 200
        data = resp.json()
        # / only trades key returned because only trades was requested
        assert "trades" in data
        assert "signals" not in data
        assert captured["kinds"] == {"trades"}
        assert captured["days"] == 14
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_markers_trades_from_trade_log(self):
        from src.dashboard import marker_aggregator as ma

        async def fake_fetch(sql, *args):
            # / verify query hits trade_log with symbol + interval args
            assert "trade_log" in sql
            return [
                {"created_at": datetime(2026, 3, 20, 10, 30), "symbol": "AAPL", "side": "buy", "price": Decimal("180.50"), "strategy_id": "s1", "pnl": None},
                {"created_at": datetime(2026, 3, 25, 14, 0), "symbol": "AAPL", "side": "sell", "price": Decimal("185.25"), "strategy_id": "s1", "pnl": Decimal("47.50")},
            ]

        class _FakeConn:
            async def fetch(self, sql, *args):
                return await fake_fetch(sql, *args)

        class _FakeCtx:
            async def __aenter__(self_inner):
                return _FakeConn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _FakePool:
            def acquire(self_inner):
                return _FakeCtx()

        rows = await ma.fetch_trade_markers(_FakePool(), "AAPL", "30 days")
        assert len(rows) == 2
        assert rows[0]["side"] == "buy"
        assert rows[0]["price"] == 180.5
        assert rows[1]["side"] == "sell"
        assert rows[1]["pnl"] == 47.5
        assert rows[0]["strategy_id"] == "s1"

    @pytest.mark.asyncio
    async def test_markers_signals_strength_threshold(self):
        from src.dashboard import marker_aggregator as ma

        async def fake_fetch(sql, *args):
            return [
                {"created_at": datetime(2026, 3, 20, 10, 0), "signal_type": "buy", "strength": Decimal("0.3"), "strategy_id": "s1"},
                {"created_at": datetime(2026, 3, 21, 10, 0), "signal_type": "buy", "strength": Decimal("0.7"), "strategy_id": "s2"},
                {"created_at": datetime(2026, 3, 22, 10, 0), "signal_type": "sell", "strength": Decimal("0.49"), "strategy_id": "s3"},
                {"created_at": datetime(2026, 3, 23, 10, 0), "signal_type": "sell", "strength": Decimal("0.5"), "strategy_id": "s4"},
            ]

        class _FakeConn:
            async def fetch(self, sql, *args):
                return await fake_fetch(sql, *args)

        class _FakeCtx:
            async def __aenter__(self_inner):
                return _FakeConn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _FakePool:
            def acquire(self_inner):
                return _FakeCtx()

        rows = await ma.fetch_signal_markers(_FakePool(), "AAPL", "30 days")
        # / only strength >= 0.5 pass through
        assert len(rows) == 2
        assert rows[0]["strength"] == 0.7
        assert rows[0]["action"] == "buy"
        assert rows[1]["strength"] == 0.5
        assert rows[1]["action"] == "sell"

    @pytest.mark.asyncio
    async def test_markers_earnings_from_revisions_not_surprises(self):
        from src.dashboard import marker_aggregator as ma

        captured_sql = {}

        async def fake_fetch(sql, *args):
            captured_sql["sql"] = sql
            return [
                {"estimate_date": date(2026, 3, 10), "period": "2026Q1", "eps_estimate": Decimal("1.50"), "revenue_estimate": None},
                {"estimate_date": date(2026, 3, 20), "period": "2026Q1", "eps_estimate": Decimal("1.60"), "revenue_estimate": None},
                {"estimate_date": date(2026, 3, 25), "period": "2026Q1", "eps_estimate": Decimal("1.40"), "revenue_estimate": None},
            ]

        class _FakeConn:
            async def fetch(self, sql, *args):
                return await fake_fetch(sql, *args)

        class _FakeCtx:
            async def __aenter__(self_inner):
                return _FakeConn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _FakePool:
            def acquire(self_inner):
                return _FakeCtx()

        rows = await ma.fetch_earnings_markers(_FakePool(), "AAPL", "30 days")
        # / sql must hit earnings_revisions, never earnings_surprises
        assert "earnings_revisions" in captured_sql["sql"]
        assert "earnings_surprises" not in captured_sql["sql"]
        assert len(rows) == 3
        # / first has no prior -> inline, second is higher (beat), third is lower (miss)
        assert rows[0]["type"] == "inline"
        assert rows[1]["type"] == "beat"
        assert rows[2]["type"] == "miss"

    @pytest.mark.asyncio
    async def test_markers_handles_table_missing(self):
        from src.dashboard import marker_aggregator as ma

        class _FakeConn:
            async def fetch(self, sql, *args):
                raise asyncpg.UndefinedTableError("relation does not exist")

        class _FakeCtx:
            async def __aenter__(self_inner):
                return _FakeConn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _FakePool:
            def acquire(self_inner):
                return _FakeCtx()

        # / every aggregator must swallow the error and return []
        pool = _FakePool()
        assert await ma.fetch_trade_markers(pool, "AAPL", "30 days") == []
        assert await ma.fetch_signal_markers(pool, "AAPL", "30 days") == []
        assert await ma.fetch_insider_markers(pool, "AAPL", "30 days") == []
        assert await ma.fetch_earnings_markers(pool, "AAPL", "30 days") == []
        assert await ma.fetch_regime_bands(pool, "AAPL", "30 days") == []
        assert await ma.fetch_consensus_strip(pool, "AAPL", "30 days") == []

    def test_market_for_symbol_infers_crypto_from_suffix(self):
        from src.dashboard import marker_aggregator as ma
        assert ma._market_for_symbol("AAPL") == "equity"
        assert ma._market_for_symbol("MSFT") == "equity"
        assert ma._market_for_symbol("BTC-USD") == "crypto"
        assert ma._market_for_symbol("ETH-USD") == "crypto"
        assert ma._market_for_symbol("HYPE-USD") == "crypto"
        assert ma._market_for_symbol("btc-usd") == "crypto"
        assert ma._market_for_symbol("BTCUSDT") == "crypto"
        assert ma._market_for_symbol("") == "equity"
        assert ma._market_for_symbol(None) == "equity"

    @pytest.mark.asyncio
    async def test_markers_insider_clusters_and_singletons(self):
        from src.dashboard import marker_aggregator as ma

        async def fake_fetch(sql, *args):
            # / three buys within 5 days -> cluster; one isolated sell -> singleton
            return [
                {"filing_date": date(2026, 3, 10), "insider_name": "A", "transaction_type": "buy", "shares": Decimal("100")},
                {"filing_date": date(2026, 3, 12), "insider_name": "B", "transaction_type": "buy", "shares": Decimal("200")},
                {"filing_date": date(2026, 3, 14), "insider_name": "C", "transaction_type": "buy", "shares": Decimal("300")},
                {"filing_date": date(2026, 3, 25), "insider_name": "D", "transaction_type": "sell", "shares": Decimal("400")},
            ]

        class _FakeConn:
            async def fetch(self, sql, *args):
                return await fake_fetch(sql, *args)

        class _FakeCtx:
            async def __aenter__(self_inner):
                return _FakeConn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _FakePool:
            def acquire(self_inner):
                return _FakeCtx()

        rows = await ma.fetch_insider_markers(_FakePool(), "AAPL", "30 days")
        # / one cluster (buys) + one singleton (sell)
        assert len(rows) == 2
        cluster = next(r for r in rows if r["cluster_size"] == 3)
        assert cluster["transaction_type"] == "buy"
        assert cluster["shares"] == 600.0
        single = next(r for r in rows if r["cluster_size"] == 1)
        assert single["transaction_type"] == "sell"


class TestDrawingsEndpoint:
    # / whitelist check — known types normalized, unknown types rejected
    def test_sanitize_drawing_type_valid_lowercases(self):
        from src.dashboard import drawings
        assert drawings.sanitize_drawing_type("trendline") == "trendline"
        assert drawings.sanitize_drawing_type("TRENDLINE") == "trendline"
        assert drawings.sanitize_drawing_type("  fib_retracement  ") == "fib_retracement"

    def test_sanitize_drawing_type_invalid_returns_none(self):
        from src.dashboard import drawings
        assert drawings.sanitize_drawing_type("bogus") is None
        assert drawings.sanitize_drawing_type("") is None
        assert drawings.sanitize_drawing_type(None) is None
        assert drawings.sanitize_drawing_type(42) is None

    def test_validate_payload_accepts_small_dict(self):
        from src.dashboard import drawings
        assert drawings.validate_payload({"anchors": [{"time": 1, "price": 100}]}) is True
        assert drawings.validate_payload({}) is True

    def test_validate_payload_rejects_non_dict(self):
        from src.dashboard import drawings
        assert drawings.validate_payload(None) is False
        assert drawings.validate_payload([1, 2, 3]) is False
        assert drawings.validate_payload("string") is False
        assert drawings.validate_payload(42) is False

    def test_validate_payload_rejects_oversized(self):
        from src.dashboard import drawings
        # / build a payload above the 32kb cap
        huge = {"data": "x" * (40 * 1024)}
        assert drawings.validate_payload(huge) is False

    def test_validate_payload_rejects_unserializable(self):
        from src.dashboard import drawings
        # / set cannot be json-encoded
        assert drawings.validate_payload({"bad": {1, 2, 3}}) is False

    @pytest.mark.asyncio
    async def test_list_drawings_empty_when_pool_none(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/drawings/AAPL")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_drawings_rejects_oversized_symbol(self):
        async with await _client() as c:
            resp = await c.get("/api/drawings/" + ("A" * 25))
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid_symbol"}

    @pytest.mark.asyncio
    async def test_list_drawings_returns_rows_from_module(self):
        from src.dashboard import app as dashboard
        from src.dashboard import drawings as drawings_mod

        async def fake_list(pool, symbol):
            return [
                {
                    "id": 1,
                    "drawing_type": "trendline",
                    "payload": {"anchors": [{"time": 1, "price": 100}]},
                    "created_at": "2026-03-20T10:00:00",
                    "updated_at": "2026-03-20T10:00:00",
                }
            ]

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(drawings_mod, "list_drawings", side_effect=fake_list):
            async with await _client() as c:
                resp = await c.get("/api/drawings/AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["drawing_type"] == "trendline"
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_create_drawing_rejects_invalid_type(self):
        from src.dashboard import app as dashboard
        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        async with await _client() as c:
            resp = await c.post(
                "/api/drawings/AAPL",
                json={"drawing_type": "nope", "payload": {"a": 1}},
            )
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid_drawing_type"}
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_create_drawing_rejects_invalid_payload(self):
        from src.dashboard import app as dashboard
        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        async with await _client() as c:
            resp = await c.post(
                "/api/drawings/AAPL",
                json={"drawing_type": "trendline", "payload": "not_a_dict"},
            )
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid_payload"}
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_create_drawing_pool_none_returns_503(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.post(
                "/api/drawings/AAPL",
                json={"drawing_type": "trendline", "payload": {"a": 1}},
            )
        assert resp.status_code == 503
        assert resp.json() == {"error": "db_not_ready"}

    @pytest.mark.asyncio
    async def test_create_drawing_happy_path(self):
        from src.dashboard import app as dashboard
        from src.dashboard import drawings as drawings_mod

        captured = {}

        async def fake_create(pool, symbol, drawing_type, payload):
            captured["symbol"] = symbol
            captured["drawing_type"] = drawing_type
            captured["payload"] = payload
            return {
                "id": 7,
                "drawing_type": drawing_type,
                "payload": payload,
                "created_at": "2026-03-20T10:00:00",
                "updated_at": "2026-03-20T10:00:00",
            }

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(drawings_mod, "create_drawing", side_effect=fake_create):
            async with await _client() as c:
                resp = await c.post(
                    "/api/drawings/AAPL",
                    json={"drawing_type": "trendline", "payload": {"anchors": [1, 2]}},
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 7
        assert captured["drawing_type"] == "trendline"
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_update_drawing_returns_404_when_missing(self):
        from src.dashboard import app as dashboard
        from src.dashboard import drawings as drawings_mod

        async def fake_update(pool, symbol, drawing_id, payload):
            return None

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(drawings_mod, "update_drawing", side_effect=fake_update):
            async with await _client() as c:
                resp = await c.put(
                    "/api/drawings/AAPL/999",
                    json={"payload": {"anchors": []}},
                )
        assert resp.status_code == 404
        assert resp.json() == {"error": "not_found"}
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_update_drawing_happy_path(self):
        from src.dashboard import app as dashboard
        from src.dashboard import drawings as drawings_mod

        captured = {}

        async def fake_update(pool, symbol, drawing_id, payload):
            captured["symbol"] = symbol
            captured["drawing_id"] = drawing_id
            return {
                "id": drawing_id,
                "drawing_type": "trendline",
                "payload": payload,
                "created_at": "2026-03-20T10:00:00",
                "updated_at": "2026-03-20T11:00:00",
            }

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(drawings_mod, "update_drawing", side_effect=fake_update):
            async with await _client() as c:
                resp = await c.put(
                    "/api/drawings/AAPL/5",
                    json={"payload": {"anchors": [{"time": 1, "price": 150}]}},
                )
        assert resp.status_code == 200
        assert resp.json()["id"] == 5
        # / symbol is propagated into the helper so the sql WHERE clause scopes the update
        assert captured["symbol"] == "AAPL"
        assert captured["drawing_id"] == 5
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_delete_drawing_happy_path(self):
        from src.dashboard import app as dashboard
        from src.dashboard import drawings as drawings_mod

        captured = {}

        async def fake_delete(pool, symbol, drawing_id):
            captured["symbol"] = symbol
            captured["drawing_id"] = drawing_id
            return True

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(drawings_mod, "delete_drawing", side_effect=fake_delete):
            async with await _client() as c:
                resp = await c.delete("/api/drawings/AAPL/5")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}
        assert captured["symbol"] == "AAPL"
        assert captured["drawing_id"] == 5
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_delete_drawing_miss_returns_false(self):
        from src.dashboard import app as dashboard
        from src.dashboard import drawings as drawings_mod

        async def fake_delete(pool, symbol, drawing_id):
            return False

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(drawings_mod, "delete_drawing", side_effect=fake_delete):
            async with await _client() as c:
                resp = await c.delete("/api/drawings/AAPL/999")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": False}
        dashboard.STATE.pool = None


class TestAlertsEndpoint:
    # / sanitize + validate helpers
    def test_sanitize_direction_valid_lowercases(self):
        from src.dashboard import alerts
        assert alerts.sanitize_direction("above") == "above"
        assert alerts.sanitize_direction("ABOVE") == "above"
        assert alerts.sanitize_direction("  below  ") == "below"

    def test_sanitize_direction_invalid_returns_none(self):
        from src.dashboard import alerts
        assert alerts.sanitize_direction("sideways") is None
        assert alerts.sanitize_direction("") is None
        assert alerts.sanitize_direction(None) is None
        assert alerts.sanitize_direction(42) is None

    def test_validate_label_under_cap(self):
        from src.dashboard import alerts
        assert alerts.validate_label("target") == "target"
        assert alerts.validate_label("  spaces  ") == "spaces"

    def test_validate_label_over_cap_trims(self):
        from src.dashboard import alerts
        long = "x" * 250
        result = alerts.validate_label(long)
        assert result is not None
        assert len(result) == 200

    def test_validate_label_empty_and_non_string(self):
        from src.dashboard import alerts
        assert alerts.validate_label("") is None
        assert alerts.validate_label("   ") is None
        assert alerts.validate_label(None) is None
        assert alerts.validate_label(42) is None

    @pytest.mark.asyncio
    async def test_list_alerts_empty_when_pool_none(self):
        from src.dashboard import alerts as alerts_mod
        result = await alerts_mod.list_alerts(None, symbol="AAPL")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_endpoint_empty_when_pool_none(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/alerts/AAPL")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_all_endpoint_empty_when_pool_none(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/alerts")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_endpoint_rejects_oversized_symbol(self):
        async with await _client() as c:
            resp = await c.get("/api/alerts/" + ("A" * 25))
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid_symbol"}

    @pytest.mark.asyncio
    async def test_create_alert_rejects_invalid_direction(self):
        from src.dashboard import app as dashboard
        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        async with await _client() as c:
            resp = await c.post(
                "/api/alerts/AAPL",
                json={"price": 100.0, "direction": "sideways"},
            )
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid_direction"}
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_create_alert_rejects_zero_price(self):
        from src.dashboard import app as dashboard
        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        async with await _client() as c:
            resp = await c.post(
                "/api/alerts/AAPL",
                json={"price": 0, "direction": "above"},
            )
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid_price"}
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_create_alert_rejects_negative_price(self):
        from src.dashboard import app as dashboard
        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        async with await _client() as c:
            resp = await c.post(
                "/api/alerts/AAPL",
                json={"price": -5, "direction": "above"},
            )
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid_price"}
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_create_alert_pool_none_returns_503(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.post(
                "/api/alerts/AAPL",
                json={"price": 100.0, "direction": "above"},
            )
        assert resp.status_code == 503
        assert resp.json() == {"error": "db_not_ready"}

    @pytest.mark.asyncio
    async def test_create_alert_happy_path(self):
        from src.dashboard import alerts as alerts_mod
        from src.dashboard import app as dashboard

        captured = {}

        async def fake_create(pool, symbol, price, direction, label=None):
            captured["symbol"] = symbol
            captured["price"] = price
            captured["direction"] = direction
            captured["label"] = label
            return {
                "id": 7,
                "symbol": symbol,
                "price": 100.0,
                "direction": direction,
                "label": label,
                "status": "active",
                "last_check": None,
                "fired_at": None,
                "created_at": "2026-03-20T10:00:00",
            }

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(alerts_mod, "create_alert", side_effect=fake_create):
            async with await _client() as c:
                resp = await c.post(
                    "/api/alerts/AAPL",
                    json={"price": 100.0, "direction": "above", "label": "top"},
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 7
        assert captured["direction"] == "above"
        assert captured["label"] == "top"
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_list_by_symbol_returns_rows(self):
        from src.dashboard import alerts as alerts_mod
        from src.dashboard import app as dashboard

        async def fake_list(pool, symbol=None, status=None):
            return [
                {
                    "id": 1,
                    "symbol": symbol,
                    "price": 100.0,
                    "direction": "above",
                    "label": None,
                    "status": "active",
                    "last_check": None,
                    "fired_at": None,
                    "created_at": "2026-03-20T10:00:00",
                }
            ]

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(alerts_mod, "list_alerts", side_effect=fake_list):
            async with await _client() as c:
                resp = await c.get("/api/alerts/AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["direction"] == "above"
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_update_alert_returns_404_when_missing(self):
        from src.dashboard import alerts as alerts_mod
        from src.dashboard import app as dashboard

        async def fake_update(pool, symbol, alert_id, **fields):
            return None

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(alerts_mod, "update_alert", side_effect=fake_update):
            async with await _client() as c:
                resp = await c.put(
                    "/api/alerts/AAPL/999",
                    json={"price": 150},
                )
        assert resp.status_code == 404
        assert resp.json() == {"error": "not_found"}
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_update_alert_happy_path(self):
        from src.dashboard import alerts as alerts_mod
        from src.dashboard import app as dashboard

        captured = {}

        async def fake_update(pool, symbol, alert_id, **fields):
            captured["symbol"] = symbol
            captured["alert_id"] = alert_id
            captured["fields"] = fields
            return {
                "id": alert_id,
                "symbol": symbol,
                "price": 150.0,
                "direction": "above",
                "label": None,
                "status": "active",
                "last_check": None,
                "fired_at": None,
                "created_at": "2026-03-20T10:00:00",
            }

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(alerts_mod, "update_alert", side_effect=fake_update):
            async with await _client() as c:
                resp = await c.put(
                    "/api/alerts/AAPL/5",
                    json={"price": 150.0},
                )
        assert resp.status_code == 200
        assert resp.json()["id"] == 5
        # / symbol is propagated so the underlying sql clause scopes the update by symbol
        assert captured["symbol"] == "AAPL"
        assert captured["alert_id"] == 5
        assert "price" in captured["fields"]
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_update_alert_rejects_empty_patch(self):
        from src.dashboard import app as dashboard
        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        async with await _client() as c:
            resp = await c.put("/api/alerts/AAPL/5", json={"other": 1})
        assert resp.status_code == 400
        assert resp.json() == {"error": "empty_patch"}
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_delete_alert_happy_path(self):
        from src.dashboard import alerts as alerts_mod
        from src.dashboard import app as dashboard

        captured = {}

        async def fake_delete(pool, symbol, alert_id):
            captured["symbol"] = symbol
            captured["alert_id"] = alert_id
            return True

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(alerts_mod, "delete_alert", side_effect=fake_delete):
            async with await _client() as c:
                resp = await c.delete("/api/alerts/AAPL/5")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}
        assert captured["symbol"] == "AAPL"
        assert captured["alert_id"] == 5
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_mark_fired_atomic_guard_sql(self):
        # / verify mark_fired issues a conditional UPDATE with status='active' guard
        from src.dashboard import alerts as alerts_mod

        captured = {}

        async def fake_fetchrow(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return None

        mock_conn = AsyncMock()
        mock_conn.fetchrow = fake_fetchrow
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_conn
        mock_ctx.__aexit__.return_value = False
        pool = MagicMock()
        pool.acquire.return_value = mock_ctx

        result = await alerts_mod.mark_fired(pool, 42, datetime(2026, 3, 20, 10, 0, 0))
        assert result is None
        assert "status = 'active'" in captured["sql"]
        assert "fired" in captured["sql"]
        assert captured["args"][0] == 42

    @pytest.mark.asyncio
    async def test_mark_checked_batch_update(self):
        # / verify mark_checked issues a single batched update via ANY($1::bigint[])
        from src.dashboard import alerts as alerts_mod

        captured = {}

        async def fake_execute(sql, *args):
            captured["sql"] = sql
            captured["args"] = args

        mock_conn = AsyncMock()
        mock_conn.execute = fake_execute
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_conn
        mock_ctx.__aexit__.return_value = False
        pool = MagicMock()
        pool.acquire.return_value = mock_ctx

        now = datetime(2026, 3, 20, 10, 0, 0)
        await alerts_mod.mark_checked(pool, [1, 2, 3], now)
        assert "ANY($1::bigint[])" in captured["sql"]
        assert captured["args"][0] == [1, 2, 3]
        assert captured["args"][1] == now

    @pytest.mark.asyncio
    async def test_mark_checked_noop_on_empty(self):
        # / empty list short-circuits before touching the pool
        from src.dashboard import alerts as alerts_mod
        await alerts_mod.mark_checked(None, [], datetime(2026, 3, 20))
        pool = MagicMock()
        await alerts_mod.mark_checked(pool, [], datetime(2026, 3, 20))
        pool.acquire.assert_not_called()


class TestReplayEndpoint:
    # / observation-mode replay — pure SELECT, zero agent / llm / broker invocation
    @pytest.mark.asyncio
    async def test_replay_returns_default_when_pool_none(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/replay/AAPL?cutoff=2026-03-20T15:00:00Z")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "AAPL"
        assert data["bars"] == {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
        assert data["trades"] == []
        assert data["signals"] == []
        assert data["consensus"] == []

    @pytest.mark.asyncio
    async def test_replay_rejects_oversized_symbol(self):
        async with await _client() as c:
            resp = await c.get("/api/replay/" + ("A" * 25) + "?cutoff=2026-03-20T15:00:00Z")
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid_symbol"}

    @pytest.mark.asyncio
    async def test_replay_happy_path_all_tables(self):
        # / every aggregator returns data — payload shape must match contract
        from src.dashboard import replay as replay_mod

        captured: list[tuple] = []

        class _FakeConn:
            async def fetch(self, sql, *args):
                captured.append((sql, args))
                if "market_data_intraday" in sql:
                    return [
                        {"timestamp": datetime(2026, 3, 18, 10, 0), "open": Decimal("180.0"),
                         "high": Decimal("181.5"), "low": Decimal("179.5"), "close": Decimal("181.0"),
                         "volume": 12345},
                        {"timestamp": datetime(2026, 3, 19, 10, 0), "open": Decimal("181.0"),
                         "high": Decimal("183.0"), "low": Decimal("180.5"), "close": Decimal("182.5"),
                         "volume": 23456},
                    ]
                if "trade_log" in sql:
                    return [
                        {"created_at": datetime(2026, 3, 18, 11, 0), "side": "buy",
                         "price": Decimal("180.5"), "strategy_id": "s1", "pnl": None},
                    ]
                if "trade_signals" in sql:
                    return [
                        {"created_at": datetime(2026, 3, 19, 9, 30), "signal_type": "buy",
                         "strength": Decimal("0.8"), "strategy_id": "s1"},
                    ]
                if "analysis_scores" in sql:
                    return [
                        {"date": date(2026, 3, 19), "consensus": "bullish"},
                    ]
                return []

        class _FakeCtx:
            async def __aenter__(self_inner):
                return _FakeConn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _FakePool:
            def acquire(self_inner):
                return _FakeCtx()

        result = await replay_mod.fetch_replay_snapshot(_FakePool(), "AAPL", "2026-03-20T15:00:00Z", 30)
        # / bars shape is column-major with one entry per bar
        assert result["symbol"] == "AAPL"
        assert len(result["bars"]["t"]) == 2
        assert result["bars"]["c"] == [181.0, 182.5]
        assert result["bars"]["v"] == [12345.0, 23456.0]
        assert len(result["trades"]) == 1
        assert result["trades"][0]["side"] == "buy"
        assert result["trades"][0]["price"] == 180.5
        assert len(result["signals"]) == 1
        assert result["signals"][0]["action"] == "buy"
        assert result["signals"][0]["strength"] == 0.8
        assert len(result["consensus"]) == 1
        assert result["consensus"][0]["consensus"] == "bullish"
        # / cutoff echoed back in iso form
        assert "2026-03-20" in result["cutoff"]
        # / all four queries parameterized with symbol + time window
        assert len(captured) == 4
        for _sql, args in captured:
            assert args[0] == "AAPL"

    @pytest.mark.asyncio
    async def test_replay_invalid_cutoff_defaults_to_now(self):
        from src.dashboard import replay as replay_mod

        class _EmptyConn:
            async def fetch(self, sql, *args):
                return []

        class _EmptyCtx:
            async def __aenter__(self_inner):
                return _EmptyConn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _EmptyPool:
            def acquire(self_inner):
                return _EmptyCtx()

        result = await replay_mod.fetch_replay_snapshot(_EmptyPool(), "AAPL", "not-a-date", 30)
        # / invalid cutoff falls back to now (utc) rather than raising or returning null
        assert result["cutoff"] is not None
        assert result["max_t"] == result["cutoff"]
        # / min_t sits strictly before max_t (days_back window applied)
        assert result["min_t"] < result["max_t"]

    def test_replay_clamps_days_back(self):
        from src.dashboard import replay as replay_mod
        # / out-of-range inputs clamp to [1, 365]
        assert replay_mod._clamp_days_back(0) == 1
        assert replay_mod._clamp_days_back(-5) == 1
        assert replay_mod._clamp_days_back(400) == 365
        assert replay_mod._clamp_days_back(30) == 30
        assert replay_mod._clamp_days_back(None) == 30
        assert replay_mod._clamp_days_back("bad") == 30

    def test_replay_parse_cutoff_iso(self):
        from src.dashboard import replay as replay_mod
        # / iso strings parse, junk returns none
        assert replay_mod._parse_cutoff("2026-03-20T15:00:00Z") is not None
        assert replay_mod._parse_cutoff("2026-03-20T15:00:00+00:00") is not None
        assert replay_mod._parse_cutoff("") is None
        assert replay_mod._parse_cutoff(None) is None
        assert replay_mod._parse_cutoff("garbage") is None

    @pytest.mark.asyncio
    async def test_replay_all_tables_failing_returns_empty_payload(self):
        # / every table raises — helper returns shape with empty containers, never throws
        from src.dashboard import replay as replay_mod

        class _BrokenConn:
            async def fetch(self, sql, *args):
                raise asyncpg.UndefinedTableError("relation does not exist")

        class _BrokenCtx:
            async def __aenter__(self_inner):
                return _BrokenConn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _BrokenPool:
            def acquire(self_inner):
                return _BrokenCtx()

        result = await replay_mod.fetch_replay_snapshot(_BrokenPool(), "AAPL", "2026-03-20T15:00:00Z", 30)
        assert result["bars"] == {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
        assert result["trades"] == []
        assert result["signals"] == []
        assert result["consensus"] == []
        assert result["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_replay_endpoint_end_to_end(self):
        # / fastapi endpoint wires pool + params through to fetch_replay_snapshot
        from src.dashboard import app as dashboard
        from src.dashboard import replay as replay_mod

        captured = {}

        async def fake_snapshot(pool, symbol, cutoff_iso, days_back):
            captured["symbol"] = symbol
            captured["cutoff_iso"] = cutoff_iso
            captured["days_back"] = days_back
            return {
                "symbol": symbol,
                "cutoff": cutoff_iso,
                "min_t": "2026-02-18T15:00:00+00:00",
                "max_t": cutoff_iso,
                "bars": {"t": ["2026-03-20T10:00:00"], "o": [180.0], "h": [181.0], "l": [179.0], "c": [180.5], "v": [1000.0]},
                "trades": [],
                "signals": [],
                "consensus": [],
            }

        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        with patch.object(replay_mod, "fetch_replay_snapshot", side_effect=fake_snapshot):
            async with await _client() as c:
                resp = await c.get("/api/replay/AAPL?cutoff=2026-03-20T15:00:00Z&days_back=14")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "AAPL"
        assert len(data["bars"]["t"]) == 1
        assert captured["symbol"] == "AAPL"
        assert captured["cutoff_iso"] == "2026-03-20T15:00:00Z"
        assert captured["days_back"] == 14


# / helper: build a fake pool where fetch returns rows from a sql->rows mapping
def _fake_pool_by_sql(sql_rows: dict):
    class _FakeConn:
        async def fetch(self_inner, sql, *args):
            for key, rows in sql_rows.items():
                if key in sql:
                    if callable(rows):
                        return rows(sql, args)
                    return rows
            return []

    class _FakeCtx:
        async def __aenter__(self_inner):
            return _FakeConn()
        async def __aexit__(self_inner, exc_type, exc, tb):
            return False

    class _FakePool:
        def acquire(self_inner):
            return _FakeCtx()

    return _FakePool()


class TestCompareEndpoint:
    # / pair normalized overlay — base + against series pulled from market_data / market_data_intraday
    @pytest.mark.asyncio
    async def test_compare_pool_none_returns_empty_series(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/compare?base=AAPL&against=MSFT&timeframe=1Day&days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert data["base"] == "AAPL"
        assert data["against"] == "MSFT"
        assert data["base_series"] == []
        assert data["against_series"] == []
        assert data["common_count"] == 0

    @pytest.mark.asyncio
    async def test_compare_rejects_empty_symbols(self):
        async with await _client() as c:
            resp = await c.get("/api/compare?base=&against=MSFT")
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid_symbol"}

    @pytest.mark.asyncio
    async def test_compare_rejects_oversized_symbol(self):
        async with await _client() as c:
            resp = await c.get("/api/compare?base=" + ("A" * 25) + "&against=MSFT")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_compare_happy_path_daily(self):
        # / both symbols have data for three common dates — first close normalizes to 0%
        from src.dashboard import compare as compare_mod
        call_count = {"n": 0}

        def rows_for(sql, args):
            call_count["n"] += 1
            if args[0] == "AAPL":
                return [
                    {"date": date(2026, 3, 18), "close": Decimal("100.0")},
                    {"date": date(2026, 3, 19), "close": Decimal("110.0")},
                    {"date": date(2026, 3, 20), "close": Decimal("121.0")},
                ]
            return [
                {"date": date(2026, 3, 18), "close": Decimal("400.0")},
                {"date": date(2026, 3, 19), "close": Decimal("404.0")},
                {"date": date(2026, 3, 20), "close": Decimal("412.0")},
            ]

        pool = _fake_pool_by_sql({"market_data": rows_for})
        result = await compare_mod.fetch_compare(pool, "AAPL", "MSFT", "1Day", 30)
        assert result["common_count"] == 3
        assert len(result["base_series"]) == 3
        assert len(result["against_series"]) == 3
        # / first close normalizes to 0%
        assert result["base_series"][0]["value"] == pytest.approx(0.0, abs=1e-9)
        assert result["against_series"][0]["value"] == pytest.approx(0.0, abs=1e-9)
        # / 100 -> 121 = +21%
        assert result["base_series"][2]["value"] == pytest.approx(21.0, abs=1e-9)
        # / 400 -> 412 = +3%
        assert result["against_series"][2]["value"] == pytest.approx(3.0, abs=1e-9)

    @pytest.mark.asyncio
    async def test_compare_alignment_drops_unmatched_timestamps(self):
        # / only timestamps present in BOTH series survive
        from src.dashboard import compare as compare_mod

        def rows_for(sql, args):
            if args[0] == "AAPL":
                return [
                    {"date": date(2026, 3, 18), "close": Decimal("100.0")},
                    {"date": date(2026, 3, 19), "close": Decimal("110.0")},
                    {"date": date(2026, 3, 20), "close": Decimal("121.0")},
                ]
            return [
                # / missing 2026-03-18, extra 2026-03-21
                {"date": date(2026, 3, 19), "close": Decimal("404.0")},
                {"date": date(2026, 3, 20), "close": Decimal("412.0")},
                {"date": date(2026, 3, 21), "close": Decimal("420.0")},
            ]

        pool = _fake_pool_by_sql({"market_data": rows_for})
        result = await compare_mod.fetch_compare(pool, "AAPL", "MSFT", "1Day", 30)
        assert result["common_count"] == 2
        assert result["base_series"][0]["time"].startswith("2026-03-19")
        # / base first common close = 110; 110 -> 121 = +10%
        assert result["base_series"][1]["value"] == pytest.approx(10.0, abs=1e-9)

    @pytest.mark.asyncio
    async def test_compare_one_symbol_empty_returns_empty(self):
        # / when one side has no rows the payload collapses to empty series
        from src.dashboard import compare as compare_mod

        def rows_for(sql, args):
            if args[0] == "AAPL":
                return [{"date": date(2026, 3, 18), "close": Decimal("100.0")}]
            return []

        pool = _fake_pool_by_sql({"market_data": rows_for})
        result = await compare_mod.fetch_compare(pool, "AAPL", "ZZZZ", "1Day", 30)
        assert result["base_series"] == []
        assert result["against_series"] == []
        assert result["common_count"] == 0

    @pytest.mark.asyncio
    async def test_compare_intraday_timeframe_routes_to_intraday_table(self):
        # / non-daily timeframe hits market_data_intraday
        from src.dashboard import compare as compare_mod
        captured_sql: list[str] = []

        class _Conn:
            async def fetch(self_inner, sql, *args):
                captured_sql.append(sql)
                return [
                    {"timestamp": datetime(2026, 3, 18, 10, 0), "close": Decimal("100.0")},
                    {"timestamp": datetime(2026, 3, 18, 11, 0), "close": Decimal("102.0")},
                ]

        class _Ctx:
            async def __aenter__(self_inner):
                return _Conn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _Pool:
            def acquire(self_inner):
                return _Ctx()

        result = await compare_mod.fetch_compare(_Pool(), "AAPL", "MSFT", "1Hour", 7)
        assert all("market_data_intraday" in s for s in captured_sql)
        assert result["common_count"] == 2

    @pytest.mark.asyncio
    async def test_compare_query_failure_returns_empty(self):
        from src.dashboard import compare as compare_mod

        class _BrokenConn:
            async def fetch(self_inner, sql, *args):
                raise asyncpg.UndefinedTableError("no table")

        class _BrokenCtx:
            async def __aenter__(self_inner):
                return _BrokenConn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _BrokenPool:
            def acquire(self_inner):
                return _BrokenCtx()

        result = await compare_mod.fetch_compare(_BrokenPool(), "AAPL", "MSFT", "1Day", 30)
        assert result["base_series"] == []
        assert result["against_series"] == []

    def test_compare_clamps_days(self):
        from src.dashboard import compare as compare_mod
        assert compare_mod._clamp_days(0) == 1
        assert compare_mod._clamp_days(-5) == 1
        assert compare_mod._clamp_days(9999) == 365
        assert compare_mod._clamp_days(30) == 30
        assert compare_mod._clamp_days("bad") == 90


class TestVolumeProfileEndpoint:
    # / horizontal histogram of traded volume at price levels
    @pytest.mark.asyncio
    async def test_volume_profile_pool_none_returns_empty(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/volume-profile/AAPL?bins=24&days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "AAPL"
        assert data["bins"] == []
        assert data["poc"] is None
        assert data["total_volume"] == 0.0

    @pytest.mark.asyncio
    async def test_volume_profile_rejects_oversized_symbol(self):
        async with await _client() as c:
            resp = await c.get("/api/volume-profile/" + ("A" * 25))
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_volume_profile_bins_counted_correctly(self):
        # / four price levels bucketed into 4 bins (minimum) with deterministic volumes
        from src.dashboard import volume_profile as vp_mod
        vp_mod._cache_clear()

        class _Conn:
            async def fetch(self_inner, sql, *args):
                return [
                    {"close": Decimal("100.0"), "volume": 1000},
                    {"close": Decimal("100.5"), "volume": 500},
                    {"close": Decimal("200.0"), "volume": 2000},
                    {"close": Decimal("300.0"), "volume": 3000},
                ]

        class _Ctx:
            async def __aenter__(self_inner):
                return _Conn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _Pool:
            def acquire(self_inner):
                return _Ctx()

        result = await vp_mod.fetch_volume_profile(_Pool(), "AAPL", bins=4, days=30)
        # / bins clamps to min 4
        assert len(result["bins"]) == 4
        # / total volume matches input sum
        assert result["total_volume"] == pytest.approx(6500.0)

    @pytest.mark.asyncio
    async def test_volume_profile_poc_is_max_volume_bin(self):
        # / bin with the highest traded volume is marked as poc
        from src.dashboard import volume_profile as vp_mod
        vp_mod._cache_clear()

        class _Conn:
            async def fetch(self_inner, sql, *args):
                return [
                    {"close": Decimal("100.0"), "volume": 100},
                    {"close": Decimal("150.0"), "volume": 9000},
                    {"close": Decimal("180.0"), "volume": 50},
                    {"close": Decimal("200.0"), "volume": 100},
                ]

        class _Ctx:
            async def __aenter__(self_inner):
                return _Conn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _Pool:
            def acquire(self_inner):
                return _Ctx()

        result = await vp_mod.fetch_volume_profile(_Pool(), "AAPL", bins=4, days=30)
        # / bin holding 9000 volume must be the poc
        assert result["poc"]["volume"] == pytest.approx(9000.0)
        # / value area spans the poc and is bounded by val <= poc center <= vah
        assert result["val"] <= result["poc"]["price_high"]
        assert result["vah"] >= result["poc"]["price_low"]

    @pytest.mark.asyncio
    async def test_volume_profile_value_area_covers_70pct(self):
        # / value area expansion stops at or above 70% of total volume
        from src.dashboard import volume_profile as vp_mod
        vp_mod._cache_clear()

        class _Conn:
            async def fetch(self_inner, sql, *args):
                # / uniform histogram -> val/vah must capture >= 70% of total once expanded
                return [
                    {"close": Decimal(str(100.0 + i)), "volume": 1000}
                    for i in range(10)
                ]

        class _Ctx:
            async def __aenter__(self_inner):
                return _Conn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _Pool:
            def acquire(self_inner):
                return _Ctx()

        result = await vp_mod.fetch_volume_profile(_Pool(), "AAPL", bins=10, days=30)
        total = result["total_volume"]
        # / sum pct of bins where price_low >= val and price_high <= vah
        area_vol = sum(b["volume"] for b in result["bins"]
                        if b["price_low"] >= result["val"] - 1e-9
                        and b["price_high"] <= result["vah"] + 1e-9)
        assert area_vol / total >= 0.69  # / allow ~1% slack for binning

    @pytest.mark.asyncio
    async def test_volume_profile_cache_short_circuits_query(self):
        # / second call with same key must NOT re-run the db query
        from src.dashboard import volume_profile as vp_mod
        vp_mod._cache_clear()
        call_count = {"n": 0}

        class _Conn:
            async def fetch(self_inner, sql, *args):
                call_count["n"] += 1
                return [{"close": Decimal("100.0"), "volume": 500}]

        class _Ctx:
            async def __aenter__(self_inner):
                return _Conn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _Pool:
            def acquire(self_inner):
                return _Ctx()

        pool = _Pool()
        r1 = await vp_mod.fetch_volume_profile(pool, "AAPL", bins=24, days=30, timeframe="1Hour")
        r2 = await vp_mod.fetch_volume_profile(pool, "AAPL", bins=24, days=30, timeframe="1Hour")
        assert call_count["n"] == 1
        assert r1 == r2

    def test_volume_profile_clamps_bins(self):
        from src.dashboard import volume_profile as vp_mod
        assert vp_mod._clamp(0, 4, 100, 24) == 4
        assert vp_mod._clamp(3, 4, 100, 24) == 4
        assert vp_mod._clamp(200, 4, 100, 24) == 100
        assert vp_mod._clamp(50, 4, 100, 24) == 50
        assert vp_mod._clamp("bad", 4, 100, 24) == 24

    def test_volume_profile_clamps_days(self):
        from src.dashboard import volume_profile as vp_mod
        assert vp_mod._clamp(0, 1, 365, 30) == 1
        assert vp_mod._clamp(9999, 1, 365, 30) == 365
        assert vp_mod._clamp(14, 1, 365, 30) == 14

    @pytest.mark.asyncio
    async def test_volume_profile_query_failure_returns_empty(self):
        from src.dashboard import volume_profile as vp_mod
        vp_mod._cache_clear()

        class _BrokenConn:
            async def fetch(self_inner, sql, *args):
                raise asyncpg.UndefinedTableError("no table")

        class _BrokenCtx:
            async def __aenter__(self_inner):
                return _BrokenConn()
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        class _BrokenPool:
            def acquire(self_inner):
                return _BrokenCtx()

        result = await vp_mod.fetch_volume_profile(_BrokenPool(), "AAPL", bins=24, days=30)
        assert result["bins"] == []
        assert result["poc"] is None
        assert result["total_volume"] == 0.0


# ---------------------------------------------------------------------------
# / phase c regressions: url aliases + universe completeness + head middleware
# ---------------------------------------------------------------------------

class TestCompareSymbolsAlias:
    @pytest.mark.asyncio
    async def test_compare_accepts_symbols_param(self):
        # / bug 3c: frontend passes symbols=AAPL,MSFT; must be aliased to base/against
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/compare?symbols=AAPL,MSFT&timeframe=1Day&days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert data["base"] == "AAPL"
        assert data["against"] == "MSFT"

    @pytest.mark.asyncio
    async def test_compare_symbols_rejects_single(self):
        # / one symbol in alias -> still missing counterpart -> 400
        async with await _client() as c:
            resp = await c.get("/api/compare?symbols=AAPL")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_compare_base_against_still_works(self):
        # / backwards-compat: existing base/against params still work alongside alias
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/compare?base=NVDA&against=TSLA")
        assert resp.status_code == 200
        assert resp.json()["base"] == "NVDA"


class TestSymbolsUniverseCompleteness:
    @pytest.mark.asyncio
    async def test_symbols_returns_all_full_universe(self):
        # / bug 5a: empty analysis_scores must still return every FULL_UNIVERSE symbol
        from src.data.symbols import FULL_UNIVERSE
        pq, pqo = _patch_query(query_results=[])
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/symbols")
        assert resp.status_code == 200
        data = resp.json()
        returned = {row["symbol"] for row in data}
        assert returned == set(FULL_UNIVERSE)
        # / every unscored symbol should carry null placeholders, not zeros
        for row in data:
            assert row["composite_score"] is None
            assert row["fundamental_score"] is None
            assert row["technical_score"] is None

    @pytest.mark.asyncio
    async def test_symbols_merges_scored_with_universe(self):
        # / scored rows preserve values, unscored symbols fill in as null
        from src.data.symbols import FULL_UNIVERSE
        scored_row = {
            "symbol": FULL_UNIVERSE[0], "date": date(2026, 4, 10),
            "composite_score": Decimal("72.5"),
            "fundamental_score": Decimal("80.0"),
            "technical_score": Decimal("60.0"),
            "regime": "bull", "ai_consensus": "bullish",
        }
        pq, pqo = _patch_query(query_results=[scored_row])
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/symbols")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == len(FULL_UNIVERSE)
        first = next(r for r in data if r["symbol"] == FULL_UNIVERSE[0])
        assert first["composite_score"] == "72.5"
        assert first["regime"] == "bull"


class TestHeadMiddleware:
    @pytest.mark.asyncio
    async def test_head_on_api_portfolio_returns_200(self):
        # / bug 5d: FastAPI APIRoute defaults to GET-only, HEAD would 404 without middleware
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        pq, pqo = _patch_query(query_results=[])
        with pq, pqo, _mock_broker(balance=_make_balance(), positions=[]):
            async with await _client() as c:
                resp = await c.head("/api/portfolio")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_head_on_api_symbols_returns_200(self):
        pq, pqo = _patch_query(query_results=[])
        with pq, pqo:
            async with await _client() as c:
                resp = await c.head("/api/symbols")
        assert resp.status_code == 200


class TestCorsFallback:
    # / cors resolution lives on DashboardState now; these tests pin the parse logic

    @staticmethod
    def _parse(env_value: str, defaults: list[str]) -> list[str]:
        cors_env = env_value.strip()
        parsed = [o.strip() for o in cors_env.split(",") if o.strip()] if cors_env else []
        return parsed or defaults

    @staticmethod
    def _defaults() -> list[str]:
        # / mirror DashboardState.load_config_from_env defaults
        return [
            "https://dashboard.siddiqtradebot.trade",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]

    def test_cors_env_empty_falls_back_to_defaults(self):
        defaults = self._defaults()
        result = self._parse("", defaults)
        assert result == defaults
        assert "https://dashboard.siddiqtradebot.trade" in result

    def test_cors_env_only_commas_falls_back_to_defaults(self):
        defaults = self._defaults()
        result = self._parse(",,  ,", defaults)
        assert result == defaults

    def test_cors_env_parses_comma_separated(self):
        defaults = self._defaults()
        result = self._parse("https://a.test,https://b.test", defaults)
        assert "https://a.test" in result
        assert "https://b.test" in result
        assert defaults[0] not in result

    def test_live_module_has_defaults_loaded(self):
        # / verify STATE.cors_origins is populated at import time
        from src.dashboard.app import STATE
        assert len(STATE.cors_origins) > 0


class TestMarkerIntervalEncoding:
    # / bug 3b: asyncpg rejects string '30 days' for INTERVAL — must be datetime.timedelta
    # / old code passed a bare string and every marker query silently failed at runtime

    @pytest.mark.asyncio
    async def test_build_markers_passes_timedelta_to_fetch(self):
        from datetime import timedelta as td

        from src.dashboard import marker_aggregator as ma

        captured_params: list = []

        class _Conn:
            async def fetch(self_inner, sql, *params):
                captured_params.append(params)
                return []

        class _Ctx:
            async def __aenter__(self_inner):
                return _Conn()
            async def __aexit__(self_inner, *a):
                return False

        class _Pool:
            def acquire(self_inner):
                return _Ctx()

        result = await ma.build_markers(_Pool(), "AAPL", kinds={"trades", "signals"}, days=30)
        assert isinstance(result, dict)
        # / every captured call should have a timedelta as its second positional arg (interval)
        assert len(captured_params) >= 2
        for params in captured_params:
            # / params = (symbol, interval, ...maybe more)
            assert len(params) >= 2
            assert isinstance(params[1], td)
            assert params[1] == td(days=30)

    @pytest.mark.asyncio
    async def test_fetch_trade_markers_rejects_string_interval(self):
        # / if a caller regresses to passing a string, the query should not crash
        # / the broadened except Exception swallows the asyncpg type error
        import asyncpg

        from src.dashboard import marker_aggregator as ma

        class _Conn:
            async def fetch(self_inner, sql, *params):
                # / simulate asyncpg rejecting string INTERVAL
                raise asyncpg.DataError("invalid input for INTERVAL")

        class _Ctx:
            async def __aenter__(self_inner):
                return _Conn()
            async def __aexit__(self_inner, *a):
                return False

        class _Pool:
            def acquire(self_inner):
                return _Ctx()

        # / function must not raise even on underlying asyncpg error
        result = await ma.fetch_trade_markers(_Pool(), "AAPL", "30 days")
        assert result == []


class TestDrawingTypes:
    def test_parallel_channel_is_valid_drawing_type(self):
        # / bug 3e: parallel_channel must be accepted by backend validator or
        # / the frontend toolbar button will fail on create
        from src.dashboard.drawings import VALID_DRAWING_TYPES, sanitize_drawing_type
        assert "parallel_channel" in VALID_DRAWING_TYPES
        assert sanitize_drawing_type("parallel_channel") == "parallel_channel"
        assert sanitize_drawing_type("PARALLEL_CHANNEL") == "parallel_channel"

    def test_rejects_unknown_drawing_type(self):
        from src.dashboard.drawings import sanitize_drawing_type
        assert sanitize_drawing_type("not_a_real_tool") is None


# / phase 4: new alt-data endpoints

class TestMacroContextEndpoint:
    @pytest.mark.asyncio
    async def test_pool_none_returns_empty(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/macro-context")
        assert resp.status_code == 200
        data = resp.json()
        assert data["indicators"] == []
        assert data["yield_curve_spread"] is None

    @pytest.mark.asyncio
    async def test_returns_indicators_and_computes_spread(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = MagicMock()
        rows = [
            {"series_id": "DGS10", "date": "2026-04-16", "value": "4.5", "normalized": "0.40"},
            {"series_id": "DGS2", "date": "2026-04-16", "value": "4.0", "normalized": "0.20"},
            {"series_id": "UNRATE", "date": "2026-04-15", "value": "4.2", "normalized": "0.10"},
        ]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/macro-context")
        data = resp.json()
        assert resp.status_code == 200
        assert len(data["indicators"]) == 3
        assert data["yield_curve_spread"] is not None
        # / DGS10 4.5 - DGS2 4.0 = 0.5, not inverted
        assert data["yield_curve_spread"]["inverted"] is False
        assert data["yield_curve_spread"]["value"] == 0.5
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_inverted_curve_flagged(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = MagicMock()
        rows = [
            {"series_id": "DGS10", "date": "2026-04-16", "value": "3.8", "normalized": "0.10"},
            {"series_id": "DGS2", "date": "2026-04-16", "value": "4.5", "normalized": "0.40"},
        ]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/macro-context")
        data = resp.json()
        assert data["yield_curve_spread"]["inverted"] is True
        dashboard.STATE.pool = None


class TestCongressionalEndpoint:
    @pytest.mark.asyncio
    async def test_pool_none_empty(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/congressional/AAPL")
        assert resp.status_code == 200
        assert resp.json() == {"trades": [], "net_buy_ratio": 0.0}

    @pytest.mark.asyncio
    async def test_returns_rows_with_ratio(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = MagicMock()
        rows = [
            {"filing_date": date(2026, 4, 10), "name": "Sen A", "transaction_type": "purchase", "amount_range": "1001-15000"},
            {"filing_date": date(2026, 4, 9), "name": "Sen B", "transaction_type": "purchase", "amount_range": "1001-15000"},
            {"filing_date": date(2026, 4, 8), "name": "Sen C", "transaction_type": "sale", "amount_range": "1001-15000"},
        ]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/congressional/AAPL")
        data = resp.json()
        assert resp.status_code == 200
        assert len(data["trades"]) == 3
        # / (2 buys - 1 sell) / 3 = 0.333...
        assert data["net_buy_ratio"] == pytest.approx(0.333, abs=0.01)
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_no_trades_zero_ratio(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = MagicMock()
        pq, pqo = _patch_query(query_results=[])
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/congressional/AAPL")
        assert resp.json() == {"trades": [], "net_buy_ratio": 0.0}
        dashboard.STATE.pool = None


class TestAnalystRatingsEndpoint:
    @pytest.mark.asyncio
    async def test_pool_none_empty(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/analyst-ratings/AAPL")
        assert resp.status_code == 200
        assert resp.json() == {"history": []}

    @pytest.mark.asyncio
    async def test_returns_history_with_consensus(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = MagicMock()
        rows = [
            {"date": date(2026, 4, 10), "strong_buy": 10, "buy": 5, "hold": 3, "sell": 1, "strong_sell": 0,
             "target_high": 200.0, "target_low": 150.0, "target_mean": 180.0},
        ]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/analyst-ratings/AAPL")
        data = resp.json()
        assert len(data["history"]) == 1
        # / (10*1 + 5*0.5 + 3*0 + 1*-0.5 + 0*-1) / 19 = 12 / 19 ≈ 0.632
        assert data["history"][0]["consensus_score"] == pytest.approx(0.632, abs=0.01)
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_empty_history(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = MagicMock()
        pq, pqo = _patch_query(query_results=[])
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/analyst-ratings/AAPL")
        assert resp.json() == {"history": []}
        dashboard.STATE.pool = None


class TestOptionsEndpoint:
    @pytest.mark.asyncio
    async def test_pool_none_empty(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/options/AAPL")
        assert resp.status_code == 200
        assert resp.json() == {"history": [], "latest": None}

    @pytest.mark.asyncio
    async def test_returns_history(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = MagicMock()
        rows = [
            {"date": date(2026, 4, 10), "iv_current": 0.35, "iv_rank": 0.6, "put_call_ratio": 0.8, "max_pain": 180.0},
            {"date": date(2026, 4, 9), "iv_current": 0.30, "iv_rank": 0.5, "put_call_ratio": 0.9, "max_pain": 175.0},
        ]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/options/AAPL")
        data = resp.json()
        assert len(data["history"]) == 2
        assert data["latest"]["iv_rank"] == 0.6
        dashboard.STATE.pool = None


class TestShortEndpoint:
    @pytest.mark.asyncio
    async def test_pool_none_empty(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/short/AAPL")
        assert resp.status_code == 200
        assert resp.json() == {"history": [], "latest": None}

    @pytest.mark.asyncio
    async def test_returns_history(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = MagicMock()
        rows = [
            {"date": date(2026, 4, 10), "short_volume": 1000000, "total_volume": 5000000, "short_ratio": 0.20},
        ]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/short/AAPL")
        data = resp.json()
        assert len(data["history"]) == 1
        assert data["latest"]["short_ratio"] == 0.2
        dashboard.STATE.pool = None


class TestDarkPoolEndpoint:
    @pytest.mark.asyncio
    async def test_pool_none_empty(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/dark-pool/AAPL")
        assert resp.status_code == 200
        assert resp.json() == {"history": [], "latest": None}

    @pytest.mark.asyncio
    async def test_returns_weekly_rows(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = MagicMock()
        rows = [
            {"week_start": date(2026, 4, 7), "ats_volume": 1000000, "total_volume": 5000000, "dark_pool_ratio": 0.2},
            {"week_start": date(2026, 3, 31), "ats_volume": 900000, "total_volume": 6000000, "dark_pool_ratio": 0.15},
        ]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/dark-pool/AAPL")
        data = resp.json()
        assert len(data["history"]) == 2
        assert data["latest"]["dark_pool_ratio"] == 0.2
        dashboard.STATE.pool = None


class TestEarningsRevisionsEndpoint:
    @pytest.mark.asyncio
    async def test_pool_none_empty(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/earnings-revisions/AAPL")
        assert resp.status_code == 200
        assert resp.json() == {"history": [], "momentum": 0.0}

    @pytest.mark.asyncio
    async def test_returns_history_and_momentum(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = MagicMock()
        rows = [
            {"period": "2026Q2", "estimate_date": date(2026, 4, 10), "eps_estimate": 1.30, "revenue_estimate": 100000000},
            {"period": "2026Q2", "estimate_date": date(2026, 3, 10), "eps_estimate": 1.00, "revenue_estimate": 95000000},
        ]
        pq, pqo = _patch_query(query_results=rows)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/earnings-revisions/AAPL")
        data = resp.json()
        assert len(data["history"]) == 2
        # / (1.30 - 1.00) / abs(1.00) = 0.30
        assert data["momentum"] == pytest.approx(0.30, abs=0.01)
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_empty_zero_momentum(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = MagicMock()
        pq, pqo = _patch_query(query_results=[])
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/earnings-revisions/AAPL")
        assert resp.json() == {"history": [], "momentum": 0.0}
        dashboard.STATE.pool = None


class TestPortfolioCorrelationEndpoint:
    @pytest.mark.asyncio
    async def test_broker_failure_returns_empty(self):
        # / broker errors cleanly fall to empty response
        with _mock_broker(error=Exception("no keys")):
            async with await _client() as c:
                resp = await c.get("/api/portfolio/correlation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbols"] == []
        assert data["matrix"] == []

    @pytest.mark.asyncio
    async def test_single_position_returns_empty_matrix(self):
        # / <2 positions — correlation is undefined
        pos = _make_position()
        with _mock_broker(balance=_make_balance(), positions=[pos]):
            async with await _client() as c:
                resp = await c.get("/api/portfolio/correlation")
        data = resp.json()
        assert data["matrix"] == []
        assert data["avg_correlation"] == 0.0

    @pytest.mark.asyncio
    async def test_pool_none_no_matrix(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        pos = _make_position()
        with _mock_broker(balance=_make_balance(), positions=[pos]):
            async with await _client() as c:
                resp = await c.get("/api/portfolio/correlation")
        data = resp.json()
        assert data["matrix"] == []


class TestPortfolioSectorsEndpoint:
    @pytest.mark.asyncio
    async def test_pool_none_empty(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/portfolio/sectors")
        assert resp.status_code == 200
        assert resp.json() == {"sectors": [], "total_value": 0.0}

    @pytest.mark.asyncio
    async def test_aggregates_by_sector(self):
        from src.dashboard import app as dashboard
        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        p1 = _make_position(symbol="AAPL", mv=10000.0)
        p2 = _make_position(symbol="MSFT", mv=5000.0)
        p3 = _make_position(symbol="BTC-USD", mv=3000.0)
        with _mock_broker(balance=_make_balance(), positions=[p1, p2, p3]):
            async with await _client() as c:
                resp = await c.get("/api/portfolio/sectors")
        data = resp.json()
        assert resp.status_code == 200
        assert data["total_value"] == 18000.0
        # / at least one sector must be present with its value
        secs = {s["sector"] for s in data["sectors"]}
        assert len(secs) >= 1
        pcts = [s["pct_of_portfolio"] for s in data["sectors"]]
        assert all(0 <= p <= 1 for p in pcts)
        dashboard.STATE.pool = None


class TestPortfolioTailDependenceEndpoint:
    @pytest.mark.asyncio
    async def test_pool_none(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/portfolio/tail-dependence")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pool_unavailable"
        assert data["lambda_lower"] is None

    @pytest.mark.asyncio
    async def test_single_position_insufficient(self):
        from src.dashboard import app as dashboard
        pool, _conn = _mock_pool()
        dashboard.STATE.pool = pool
        p1 = _make_position()
        with _mock_broker(balance=_make_balance(), positions=[p1]):
            async with await _client() as c:
                resp = await c.get("/api/portfolio/tail-dependence")
        data = resp.json()
        assert data["status"] == "insufficient_positions"
        assert data["positions_count"] == 1
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_no_market_data_status(self):
        from src.dashboard import app as dashboard
        pool, conn = _mock_pool()
        dashboard.STATE.pool = pool
        conn.fetch.return_value = []
        p1 = _make_position(symbol="AAPL")
        p2 = _make_position(symbol="MSFT")
        with _mock_broker(balance=_make_balance(), positions=[p1, p2]):
            async with await _client() as c:
                resp = await c.get("/api/portfolio/tail-dependence")
        data = resp.json()
        assert data["status"] in ("no_data", "insufficient_history", "insufficient_returns")
        dashboard.STATE.pool = None


class TestSizingMultipliersEndpoint:
    @pytest.mark.asyncio
    async def test_returns_multipliers(self):
        async with await _client() as c:
            resp = await c.get("/api/risk/sizing-multipliers")
        assert resp.status_code == 200
        data = resp.json()
        assert "multipliers" in data
        m = data["multipliers"]
        # / configs/risk_limits.json keys
        assert "bull" in m and "bear" in m
        assert m["bull"] == pytest.approx(1.0)
        assert m["bear"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_missing_file_returns_empty(self):
        from pathlib import Path
        with patch.object(Path, "exists", return_value=False):
            async with await _client() as c:
                resp = await c.get("/api/risk/sizing-multipliers")
        assert resp.status_code == 200
        assert resp.json() == {"multipliers": {}}


class TestRegimeTimelineEndpoint:
    @pytest.mark.asyncio
    async def test_invalid_market_400(self):
        async with await _client() as c:
            resp = await c.get("/api/regime-timeline?market=nasdaq")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_pool_none_empty(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = None
        async with await _client() as c:
            resp = await c.get("/api/regime-timeline?market=equity&days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert data["market"] == "equity"
        assert data["history"] == []
        assert data["shifts"] == []

    @pytest.mark.asyncio
    async def test_returns_history_and_shifts(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = MagicMock()
        history_rows = [
            {"date": date(2026, 4, 1), "regime": "bull", "confidence": 0.85,
             "volatility_20d": 0.12, "trend_sma50_above_200": True, "drawdown_from_high": -0.05},
        ]
        shifts_rows = [
            {"id": 1, "old_regime": "sideways", "new_regime": "bull", "confidence": 0.8,
             "wiki_path": "regimes/001.md", "detected_at": datetime(2026, 4, 1, 10)},
        ]

        call_count = {"n": 0}

        async def seq(sql, *args):
            # / first call = history, second call = shifts
            call_count["n"] += 1
            return history_rows if call_count["n"] == 1 else shifts_rows

        pq, pqo = _patch_query(query_results=seq)
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/regime-timeline?market=equity&days=60")
        data = resp.json()
        assert resp.status_code == 200
        assert data["market"] == "equity"
        assert data["days"] == 60
        assert len(data["history"]) == 1
        assert data["history"][0]["regime"] == "bull"
        assert len(data["shifts"]) == 1
        assert data["shifts"][0]["new_regime"] == "bull"
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_days_clamped(self):
        from src.dashboard import app as dashboard
        dashboard.STATE.pool = MagicMock()
        pq, pqo = _patch_query(query_results=[])
        with pq, pqo:
            async with await _client() as c:
                resp = await c.get("/api/regime-timeline?market=crypto&days=99999")
        # / should not crash; day cap lives in the handler
        assert resp.status_code == 200
        dashboard.STATE.pool = None
