# / tests for strategy_mutator

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from src.evolution.strategy_mutator import (
    _build_mutation_prompt,
    _parse_json_response,
    _random_tweak,
    mutate_strategy,
)


def _base_config() -> dict:
    # / minimal valid config for mutation tests
    return {
        "id": "strategy_killed",
        "name": "killed_strat",
        "version": 1,
        "asset_class": "stocks",
        "universe": "all_stocks",
        "entry_conditions": {
            "operator": "AND",
            "signals": [
                {"indicator": "rsi", "condition": "below", "threshold": 30, "period": 14},
                {"indicator": "volume", "condition": "above_average", "multiplier": 1.5, "period": 20},
            ],
        },
        "exit_conditions": {
            "stop_loss": {"type": "fixed_pct", "pct": 0.05},
            "time_exit": {"max_holding_days": 20},
        },
        "position_sizing": {
            "method": "fixed_pct",
            "max_position_pct": 0.03,
        },
        "metadata": {
            "generation": 2,
            "status": "killed",
        },
    }


def _top_config() -> dict:
    return {
        "id": "strategy_top",
        "name": "top_strat",
        "version": 1,
        "asset_class": "stocks",
        "universe": "all_stocks",
        "entry_conditions": {
            "operator": "AND",
            "signals": [
                {"indicator": "macd", "condition": "crossover_bullish"},
            ],
        },
        "exit_conditions": {
            "stop_loss": {"type": "fixed_pct", "pct": 0.03},
        },
        "position_sizing": {
            "method": "fixed_pct",
            "max_position_pct": 0.04,
        },
        "metadata": {"generation": 5, "status": "live"},
    }


def _valid_llm_response() -> str:
    # / a valid json response that would pass validation
    config = {
        "id": "strategy_new",
        "name": "evolved_strat",
        "version": 1,
        "asset_class": "stocks",
        "universe": "all_stocks",
        "entry_conditions": {
            "operator": "AND",
            "signals": [
                {"indicator": "rsi", "condition": "below", "threshold": 25, "period": 14},
            ],
        },
        "exit_conditions": {
            "stop_loss": {"type": "fixed_pct", "pct": 0.04},
        },
        "position_sizing": {
            "method": "fixed_pct",
            "max_position_pct": 0.03,
        },
        "metadata": {"generation": 1, "status": "backtest_pending"},
    }
    return json.dumps(config)


# ────────────────────────────────────────────────────────────────
# mutate_strategy with llm
# ────────────────────────────────────────────────────────────────


class TestMutateWithLLM:
    @pytest.mark.asyncio
    async def test_mutate_with_valid_llm_response(self):
        # / mock anthropic returning valid json
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("src.evolution.strategy_mutator._call_deepseek_v4", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = _valid_llm_response()
                result = await mutate_strategy(
                    _base_config(), _top_config(), [],
                    rng=np.random.default_rng(42),
                )

        assert isinstance(result, list)
        assert len(result) >= 1
        r = result[0]
        assert r["created_by"] == "evolution_agent"
        assert r["parent_id"] == "strategy_killed"
        assert r["metadata"]["status"] == "backtest_pending"
        assert r["metadata"]["generation"] == 3  # / killed was gen 2, so 2+1=3
        assert r["id"].startswith("strategy_")

    @pytest.mark.asyncio
    async def test_mutate_retries_on_invalid_json(self):
        # / first 2 attempts invalid, 3rd valid
        call_count = 0

        async def _mock_llm(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return "not valid json at all {"
            return _valid_llm_response()

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("src.evolution.strategy_mutator._call_deepseek_v4", side_effect=_mock_llm):
                result = await mutate_strategy(
                    _base_config(), _top_config(), [],
                    rng=np.random.default_rng(42),
                )

        assert call_count == 3
        assert result[0]["created_by"] == "evolution_agent"

    @pytest.mark.asyncio
    async def test_mutate_all_retries_fail_falls_back(self):
        # / mock anthropic always returning garbage, verify random_tweak used
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("src.evolution.strategy_mutator._call_deepseek_v4", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = "garbage not json"
                result = await mutate_strategy(
                    _base_config(), _top_config(), [],
                    rng=np.random.default_rng(42),
                )

        # / should fall back to random_tweak
        assert result[0]["created_by"] == "random_mutation"
        assert result[0]["parent_id"] == "strategy_killed"
        assert mock_llm.call_count == 3

    @pytest.mark.asyncio
    async def test_mutate_no_api_key(self):
        # / no ANTHROPIC_API_KEY env var, goes straight to random_tweak
        with patch.dict(os.environ, {}, clear=True):
            # / also clear ANTHROPIC_API_KEY if set
            os.environ.pop("ANTHROPIC_API_KEY", None)
            result = await mutate_strategy(
                _base_config(), _top_config(), [],
                rng=np.random.default_rng(42),
            )

        assert result[0]["created_by"] == "random_mutation"
        assert result[0]["parent_id"] == "strategy_killed"


# ────────────────────────────────────────────────────────────────
# _random_tweak
# ────────────────────────────────────────────────────────────────


class TestRandomTweak:
    def test_random_tweak_produces_valid_config(self):
        # / output has all required fields
        rng = np.random.default_rng(42)
        config = _base_config()
        result = _random_tweak(config, rng)

        assert "id" in result
        assert "name" in result
        assert "entry_conditions" in result
        assert "exit_conditions" in result
        assert "position_sizing" in result
        assert "metadata" in result

    def test_random_tweak_changes_at_least_one_param(self):
        # / compare original vs tweaked, something should differ
        rng = np.random.default_rng(42)
        config = _base_config()
        result = _random_tweak(config, rng)

        # / at minimum the id changes
        assert result["id"] != config["id"]

        # / check that some signal param or stop loss changed
        orig_signals = config["entry_conditions"]["signals"]
        new_signals = result["entry_conditions"]["signals"]
        orig_stop = config["exit_conditions"]["stop_loss"]
        new_stop = result["exit_conditions"]["stop_loss"]
        orig_time = config["exit_conditions"]["time_exit"]
        new_time = result["exit_conditions"]["time_exit"]

        something_changed = (
            orig_signals != new_signals
            or orig_stop != new_stop
            or orig_time != new_time
        )
        assert something_changed

    def test_random_tweak_assigns_new_id(self):
        rng = np.random.default_rng(42)
        config = _base_config()
        result = _random_tweak(config, rng)
        assert result["id"] != config["id"]
        assert result["id"].startswith("strategy_")

    def test_random_tweak_preserves_structure(self):
        rng = np.random.default_rng(42)
        config = _base_config()
        result = _random_tweak(config, rng)
        assert result["name"] == config["name"]
        assert result["asset_class"] == config["asset_class"]
        assert result["universe"] == config["universe"]
        assert len(result["entry_conditions"]["signals"]) == len(config["entry_conditions"]["signals"])

    def test_random_tweak_bumps_generation(self):
        rng = np.random.default_rng(42)
        config = _base_config()
        result = _random_tweak(config, rng)
        assert result["metadata"]["generation"] == 3  # / original was 2

    def test_random_tweak_sets_parent_id(self):
        rng = np.random.default_rng(42)
        config = _base_config()
        result = _random_tweak(config, rng)
        assert result["parent_id"] == "strategy_killed"

    def test_random_tweak_period_stays_positive(self):
        # / period should never go below 2
        rng = np.random.default_rng(42)
        config = _base_config()
        config["entry_conditions"]["signals"][0]["period"] = 2  # / minimum
        # / run many tweaks to test boundary
        for seed in range(100):
            r = np.random.default_rng(seed)
            result = _random_tweak(config, r)
            for sig in result["entry_conditions"]["signals"]:
                if "period" in sig:
                    assert sig["period"] >= 2

    def test_random_tweak_stop_loss_stays_positive(self):
        rng = np.random.default_rng(42)
        config = _base_config()
        config["exit_conditions"]["stop_loss"]["pct"] = 0.01  # / near minimum
        for seed in range(100):
            r = np.random.default_rng(seed)
            result = _random_tweak(config, r)
            pct = result["exit_conditions"]["stop_loss"].get("pct")
            if pct is not None:
                assert pct >= 0.01

    def test_random_tweak_multiplier_stays_positive(self):
        rng = np.random.default_rng(42)
        config = _base_config()
        for seed in range(100):
            r = np.random.default_rng(seed)
            result = _random_tweak(config, r)
            for sig in result["entry_conditions"]["signals"]:
                if "multiplier" in sig:
                    assert sig["multiplier"] >= 0.1

    def test_random_tweak_holding_days_stays_positive(self):
        rng = np.random.default_rng(42)
        config = _base_config()
        config["exit_conditions"]["time_exit"]["max_holding_days"] = 1
        for seed in range(100):
            r = np.random.default_rng(seed)
            result = _random_tweak(config, r)
            days = result["exit_conditions"]["time_exit"].get("max_holding_days")
            if days is not None:
                assert days >= 1

    def test_random_tweak_handles_none_values(self):
        # / signals with None values should not crash
        rng = np.random.default_rng(42)
        config = _base_config()
        config["entry_conditions"]["signals"][0]["period"] = None
        config["entry_conditions"]["signals"][0]["threshold"] = None
        config["entry_conditions"]["signals"][0]["multiplier"] = None
        config["exit_conditions"]["stop_loss"]["pct"] = None
        config["exit_conditions"]["time_exit"]["max_holding_days"] = None

        result = _random_tweak(config, rng)
        assert result["id"] != config["id"]
        # / None values should remain None (not crash)
        sig = result["entry_conditions"]["signals"][0]
        assert sig["period"] is None
        assert sig["threshold"] is None


# ────────────────────────────────────────────────────────────────
# _parse_json_response
# ────────────────────────────────────────────────────────────────


class TestParseJsonResponse:
    def test_parse_json_response_plain(self):
        data = {"key": "value", "num": 42}
        result = _parse_json_response(json.dumps(data))
        assert result == data

    def test_parse_json_response_with_fences(self):
        data = {"key": "value"}
        text = f"```json\n{json.dumps(data)}\n```"
        result = _parse_json_response(text)
        assert result == data

    def test_parse_json_response_with_fences_no_lang(self):
        data = {"key": "value"}
        text = f"```\n{json.dumps(data)}\n```"
        result = _parse_json_response(text)
        assert result == data

    def test_parse_json_response_invalid(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json_response("not valid json {{{")


# ────────────────────────────────────────────────────────────────
# _build_mutation_prompt
# ────────────────────────────────────────────────────────────────


class TestBuildPrompt:
    def test_build_prompt_includes_configs(self):
        killed = _base_config()
        top = _top_config()
        prompt = _build_mutation_prompt(killed, top, [])
        assert "strategy_killed" in prompt
        assert "strategy_top" in prompt
        assert "killed_strat" in prompt
        assert "top_strat" in prompt

    def test_build_prompt_includes_trades(self):
        trades = [
            {"symbol": "AAPL", "side": "buy", "pnl": -50.0},
            {"symbol": "MSFT", "side": "buy", "pnl": 120.0},
        ]
        prompt = _build_mutation_prompt(_base_config(), _top_config(), trades)
        assert "AAPL" in prompt
        assert "MSFT" in prompt
        assert "-50.0" in prompt
        assert "120.0" in prompt

    def test_build_prompt_no_trades(self):
        prompt = _build_mutation_prompt(_base_config(), _top_config(), [])
        assert "No recent trades" in prompt

    def test_build_prompt_limits_trades_to_5(self):
        trades = [{"symbol": f"SYM{i}", "side": "buy", "pnl": float(i)} for i in range(10)]
        prompt = _build_mutation_prompt(_base_config(), _top_config(), trades)
        # / only first 5 should appear
        assert "SYM4" in prompt
        assert "SYM5" not in prompt
