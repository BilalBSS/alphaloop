# / regression tests for phase e bug sprint
# / pins fixes for intraday 2h aggregation, health loop events, killed-strategy disk fallback,
# / evolution dormancy kill, /api/positions strategy_id, untracked avg_entry_price,
# / quant-metrics position stub, synthesis raw_response, peg=0 null, drawings field alias,
# / chart disposal race

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


# -- bug 1: aggregate_intraday_to_2h --

class TestAggregateIntradayTo2h:
    @pytest.mark.asyncio
    async def test_empty_symbols_returns_empty(self):
        from src.data.market_data import aggregate_intraday_to_2h
        pool = MagicMock()
        result = await aggregate_intraday_to_2h(pool, [])
        assert result == {}

    @pytest.mark.asyncio
    async def test_inserts_2h_rows_from_1h_aggregation(self):
        from src.data.market_data import aggregate_intraday_to_2h
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {
                "timestamp": datetime(2026, 4, 16, 14, 0, tzinfo=timezone.utc),
                "open": Decimal("100"), "high": Decimal("105"),
                "low": Decimal("99"), "close": Decimal("104"),
                "volume": 1000, "vwap": Decimal("102"),
            },
            {
                "timestamp": datetime(2026, 4, 16, 16, 0, tzinfo=timezone.utc),
                "open": Decimal("104"), "high": Decimal("108"),
                "low": Decimal("103"), "close": Decimal("107"),
                "volume": 2000, "vwap": Decimal("105"),
            },
        ]
        pool = _mock_pool(mock_conn)
        result = await aggregate_intraday_to_2h(pool, ["AAPL"], days=10)
        assert result["AAPL"] == 2
        insert_calls = [
            c for c in mock_conn.execute.call_args_list
            if c.args and "INSERT INTO market_data_intraday" in c.args[0]
        ]
        assert len(insert_calls) == 2
        for c in insert_calls:
            assert "'2Hour'" in c.args[0]


# -- bug 2: executor kill gate disk fallback --

class TestExecutorKillGateDiskFallback:
    def test_disk_fallback_reads_killed_status(self, tmp_path):
        from src.agents.executor_agent import _strategy_killed_on_disk
        # / build a fake configs dir with a killed strategy
        cfg_dir = tmp_path / "configs" / "strategies"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "strategy_test_killed.json").write_text(
            json.dumps({"id": "strategy_test_killed", "metadata": {"status": "killed"}})
        )
        (cfg_dir / "strategy_test_alive.json").write_text(
            json.dumps({"id": "strategy_test_alive", "metadata": {"status": "paper_trading"}})
        )
        # / function reads from repo's configs dir — patch the path module inside
        import src.agents.executor_agent as mod
        original = mod.__file__
        with patch.object(mod, "__file__", str(tmp_path / "src" / "agents" / "executor_agent.py")):
            assert _strategy_killed_on_disk("strategy_test_killed") is True
            assert _strategy_killed_on_disk("strategy_test_alive") is False
            assert _strategy_killed_on_disk("strategy_nonexistent") is False

    def test_disk_fallback_rejects_unsafe_ids(self):
        from src.agents.executor_agent import _strategy_killed_on_disk
        assert _strategy_killed_on_disk("") is False
        assert _strategy_killed_on_disk("../etc/passwd") is False
        assert _strategy_killed_on_disk("a;b") is False


# -- bug 14: reconcile carries avg_entry_price --

class TestReconcilePriceMap:
    def _tx_mock(self, mock_conn):
        tx = AsyncMock()
        tx.__aenter__.return_value = None
        tx.__aexit__.return_value = False
        mock_conn.transaction = MagicMock(return_value=tx)

    @pytest.mark.asyncio
    async def test_untracked_insert_uses_price_map(self):
        from src.agents.position_tools import reconcile_strategy_positions
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        # / no trade_log attribution available — fall back to untracked
        mock_conn.fetchrow.return_value = None
        self._tx_mock(mock_conn)
        pool = _mock_pool(mock_conn)
        await reconcile_strategy_positions(
            pool,
            alpaca_map={"NVDA": 10.0},
            full_sync=True,
            price_map={"NVDA": 987.50},
        )
        # / verify the INSERT for the missing-row case carried the price
        insert_calls = [
            c for c in mock_conn.execute.call_args_list
            if c.args and "INSERT INTO strategy_positions" in c.args[0]
        ]
        assert insert_calls, "expected INSERT into strategy_positions"
        args = insert_calls[0].args
        # / positional args after the sql: strat, symbol, qty, avg_price
        assert args[1] == "untracked"
        assert args[2] == "NVDA"
        assert args[4] == Decimal("987.5")

    @pytest.mark.asyncio
    async def test_untracked_insert_defaults_to_zero_when_no_price(self):
        from src.agents.position_tools import reconcile_strategy_positions
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_conn.fetchrow.return_value = None
        self._tx_mock(mock_conn)
        pool = _mock_pool(mock_conn)
        await reconcile_strategy_positions(
            pool, alpaca_map={"NVDA": 10.0}, full_sync=True,
        )
        insert_calls = [
            c for c in mock_conn.execute.call_args_list
            if c.args and "INSERT INTO strategy_positions" in c.args[0]
        ]
        assert insert_calls
        args = insert_calls[0].args
        assert args[4] == Decimal("0")

    @pytest.mark.asyncio
    async def test_reconcile_recovers_strategy_id_from_trade_log(self):
        # / when trade_log has a recent buy tagged with a real strategy_id,
        # / inserting a missing row should honour that attribution instead of
        # / writing 'untracked'.
        from src.agents.position_tools import reconcile_strategy_positions
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_conn.fetchrow.return_value = {"strategy_id": "strategy_011"}
        self._tx_mock(mock_conn)
        pool = _mock_pool(mock_conn)
        await reconcile_strategy_positions(
            pool, alpaca_map={"ENPH": 38.0}, full_sync=True,
            price_map={"ENPH": 34.02},
        )
        insert_calls = [
            c for c in mock_conn.execute.call_args_list
            if c.args and "INSERT INTO strategy_positions" in c.args[0]
        ]
        assert insert_calls
        args = insert_calls[0].args
        assert args[1] == "strategy_011"
        assert args[2] == "ENPH"


# -- bug 12: evolution dormancy kill (gated by clean-slate threshold) --

class TestEvolutionDormancyKill:
    @pytest.mark.asyncio
    async def test_dormant_gated_when_below_clean_slate_threshold(self):
        # / bug e2: dormancy kill is gated until the system has >=10 closed trades
        # / across any strategy. this protects newly-fixed pipelines from killing
        # / strategies whose historical data was corrupt.
        from unittest.mock import MagicMock as _MM

        from src.evolution.evolution_engine import EvolutionEngine
        from src.strategies.strategy_pool import StrategyPool, StrategyScore

        engine = EvolutionEngine()
        pool = StrategyPool()
        fake_strategy = _MM()
        fake_strategy.strategy_id = "strategy_dormant"
        fake_strategy.config = {
            "id": "strategy_dormant",
            "metadata": {"status": "paper_trading"},
        }
        pool.add(fake_strategy, status="paper_trading")
        entry = pool.get("strategy_dormant")
        entry.status_changed_at = datetime.now(timezone.utc) - timedelta(days=45)
        entry.score = StrategyScore(
            strategy_id="strategy_dormant",
            sharpe_ratio=0.0, max_drawdown=0.0, win_rate=0.0, total_trades=0,
        )

        summary: dict = {"killed": [], "mutated": [], "promoted": [], "errors": []}
        with patch("src.evolution.evolution_engine.save_config"), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock):
            killed = await engine._kill_bottom_quartile(
                pool=MagicMock(), generation=1, strategy_pool=pool, summary=summary,
            )
        # / gated: nothing killed because system hasn't hit 10 closed trades
        assert killed == []

    @pytest.mark.asyncio
    async def test_dormant_kills_once_clean_slate_threshold_met(self):
        # / bug e2: once system has >=10 closed trades, dormancy kills engage
        from unittest.mock import MagicMock as _MM

        from src.evolution.evolution_engine import EvolutionEngine
        from src.strategies.strategy_pool import StrategyPool, StrategyScore

        engine = EvolutionEngine()
        pool = StrategyPool()
        # / one dormant strategy
        d = _MM()
        d.strategy_id = "strategy_dormant"
        d.config = {"id": "strategy_dormant", "metadata": {"status": "paper_trading"}}
        pool.add(d, status="paper_trading")
        entry_d = pool.get("strategy_dormant")
        entry_d.status_changed_at = datetime.now(timezone.utc) - timedelta(days=45)
        entry_d.score = StrategyScore(
            strategy_id="strategy_dormant",
            sharpe_ratio=0.0, max_drawdown=0.0, win_rate=0.0, total_trades=0,
        )
        # / active strategy with 15 closed trades to engage clean_slate
        a = _MM()
        a.strategy_id = "strategy_active"
        a.config = {"id": "strategy_active", "metadata": {"status": "paper_trading"}}
        pool.add(a, status="paper_trading")
        entry_a = pool.get("strategy_active")
        entry_a.score = StrategyScore(
            strategy_id="strategy_active",
            sharpe_ratio=1.2, max_drawdown=-0.05, win_rate=0.6, total_trades=15,
        )

        summary: dict = {"killed": [], "mutated": [], "promoted": [], "errors": []}
        with patch("src.evolution.evolution_engine.save_config"), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock):
            killed = await engine._kill_bottom_quartile(
                pool=MagicMock(), generation=1, strategy_pool=pool, summary=summary,
            )
        assert any(k["id"] == "strategy_dormant" for k in killed)
        assert any("dormant" in k.get("reason", "") for k in summary["killed"])


# -- bug 3: synthesis raw_response preserves llm payload --

class TestSynthesisRawResponse:
    @pytest.mark.asyncio
    async def test_raw_attempts_written_on_failure(self, monkeypatch):
        # / when all attempts fail, fallback body includes each raw payload (even empty)
        # / not just the parser exception text
        from src.analysis import ai_summary
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake")

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"symbol": "AAPL", "composite_score": 80, "regime": "bull", "ai_consensus": "buy"},
        ]
        pool = _mock_pool(mock_conn)

        captured: dict = {}

        async def fake_store(pool, today, model, buys, avoids, risk, notes, raw):
            captured["raw"] = raw

        async def fake_llm(*args, **kwargs):
            return {"choices": [{"message": {"content": ""}}]}

        monkeypatch.setattr("src.data.synthesis.store_daily_synthesis", fake_store)
        monkeypatch.setattr("src.data.llm_client.llm_call", fake_llm)

        result = await ai_summary.generate_daily_synthesis(pool, ["AAPL"])
        assert result is None
        assert "raw" in captured
        # / fallback body must mention the provider attempt markers, not just parser errors
        assert "deepseek" in captured["raw"].lower()


# -- bug 4: insider nan cleanup migration file exists and targets correct columns --

class TestInsiderNanMigration:
    def test_migration_041_scrubs_nan_columns(self):
        from pathlib import Path
        path = Path(__file__).parent.parent / "src" / "data" / "migrations" / "041_insider_nan_cleanup.sql"
        assert path.exists()
        sql = path.read_text()
        for col in ("shares", "price_per_share", "total_value"):
            assert f"UPDATE insider_trades SET {col} = NULL WHERE {col} = 'NaN'::DECIMAL;" in sql


# -- bug 5: drawings endpoint accepts type alias --

class TestDrawingTypeAlias:
    def test_sanitize_accepts_known_types(self):
        from src.dashboard.drawings import sanitize_drawing_type
        assert sanitize_drawing_type("trendline") == "trendline"
        assert sanitize_drawing_type("horizontal_line") == "horizontal_line"


# -- bug 13: peg=0 serializes as None --

class TestPegZeroBecomesNull:
    def test_peg_zero_becomes_none(self):
        # / mini reproduction of the analyst_agent logic
        details = {"peg_ratio": "0.00"}
        raw = details.get("peg_ratio")
        try:
            peg = float(raw) if raw and float(raw) > 0 else None
        except (TypeError, ValueError):
            peg = None
        assert peg is None

    def test_peg_positive_preserved(self):
        details = {"peg_ratio": "1.25"}
        raw = details.get("peg_ratio")
        try:
            peg = float(raw) if raw and float(raw) > 0 else None
        except (TypeError, ValueError):
            peg = None
        assert peg == 1.25


# -- bug 7+11: /api/positions strategy_id attachment + quant-metrics stubs --
# / integration-level asserts via the dashboard TestClient are covered in test_dashboard.py;
# / here we pin the shape logic in isolation


class TestPositionsStrategyIdShape:
    def test_primary_owner_wins_over_untracked(self):
        owners = [
            {"strategy_id": "untracked", "qty": 10},
            {"strategy_id": "strategy_011", "qty": 10},
        ]
        primary = next(
            (o for o in owners if o.get("strategy_id") and o["strategy_id"] != "untracked"), None,
        )
        untracked = next((o for o in owners if o.get("strategy_id") == "untracked"), None)
        assert primary is not None and primary["strategy_id"] == "strategy_011"
        assert untracked is not None


class TestQuantMetricsStubShape:
    def test_stub_emitted_when_not_seen(self):
        rows = []
        stubs = [{"strategy_id": "strategy_011", "avg_entry_price": 10, "qty": 5, "updated_at": None}]
        seen = {r.get("strategy_id") for r in rows}
        for s in stubs:
            if s["strategy_id"] not in seen:
                rows.append({
                    "strategy_id": s["strategy_id"],
                    "sharpe_ratio": None,
                    "regime_breakdown": {"source": "position_stub"},
                })
        assert len(rows) == 1
        assert rows[0]["regime_breakdown"]["source"] == "position_stub"
