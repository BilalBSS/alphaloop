# / tests for wiki-guided mutation — prompt injection, 80/20 ratio, context tokens

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.evolution.evolution_engine import DEFAULT_WIKI_GUIDED_RATIO, EvolutionEngine
from src.evolution.strategy_mutator import _build_mutation_prompt


def _minimal_config() -> dict:
    return {
        "id": "sid",
        "name": "n",
        "version": 1,
        "asset_class": "stocks",
        "universe": "all_stocks",
        "entry_conditions": {
            "operator": "AND",
            "signals": [{"indicator": "rsi", "condition": "below", "threshold": 30, "period": 14}],
        },
        "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.05}},
        "position_sizing": {"method": "fixed_pct", "max_position_pct": 0.03},
        "metadata": {"generation": 1, "status": "killed"},
    }


# ──────────────────────────────────────────────────────
# prompt injection
# ──────────────────────────────────────────────────────

class TestPromptInjection:
    def test_wiki_context_emits_block(self):
        # / _build_mutation_prompt with wiki_context adds a distinct header section
        prompt = _build_mutation_prompt(
            _minimal_config(), _minimal_config(), [],
            wiki_context="Lesson: avoid trading through earnings.",
        )
        assert "## RELEVANT WIKI CONTEXT" in prompt
        assert "Lesson: avoid trading through earnings." in prompt

    def test_wiki_context_none_omits_block(self):
        prompt = _build_mutation_prompt(
            _minimal_config(), _minimal_config(), [], wiki_context=None,
        )
        assert "## RELEVANT WIKI CONTEXT" not in prompt

    def test_wiki_context_empty_string_omits_block(self):
        # / empty string should also omit the block (`if wiki_context:` is falsy)
        prompt = _build_mutation_prompt(
            _minimal_config(), _minimal_config(), [], wiki_context="",
        )
        assert "## RELEVANT WIKI CONTEXT" not in prompt


# ──────────────────────────────────────────────────────
# 80/20 ratio
# ──────────────────────────────────────────────────────

class TestWikiGuidedRatio:
    @pytest.mark.asyncio
    async def test_approximately_80_20_over_100_trials(self):
        # / with rng seeded and ratio=0.80, expect ~80 guided over 100 trials
        env = {k: v for k, v in os.environ.items() if k != "WIKI_GUIDED_RATIO"}
        with patch.dict(os.environ, env, clear=True):
            engine = EvolutionEngine(rng=np.random.default_rng(42))
        assert engine._wiki_guided_ratio == pytest.approx(DEFAULT_WIKI_GUIDED_RATIO)

        n_trials = 200
        guided = 0
        for _ in range(n_trials):
            if engine._rng.random() < engine._wiki_guided_ratio:
                guided += 1
        ratio = guided / n_trials
        # / should be within ±10% of 0.80
        assert abs(ratio - 0.80) < 0.10, f"got {ratio:.2f}, expected ~0.80"

    @pytest.mark.asyncio
    async def test_env_override_respected(self):
        # / WIKI_GUIDED_RATIO=0.5 should produce ~50%
        with patch.dict(os.environ, {"WIKI_GUIDED_RATIO": "0.5"}):
            engine = EvolutionEngine(rng=np.random.default_rng(42))
        assert engine._wiki_guided_ratio == pytest.approx(0.5)

        n_trials = 200
        guided = sum(
            1 for _ in range(n_trials) if engine._rng.random() < engine._wiki_guided_ratio
        )
        ratio = guided / n_trials
        assert abs(ratio - 0.5) < 0.10

    def test_env_invalid_falls_back_to_default(self):
        with patch.dict(os.environ, {"WIKI_GUIDED_RATIO": "not-a-number"}):
            engine = EvolutionEngine(rng=np.random.default_rng(42))
        assert engine._wiki_guided_ratio == pytest.approx(DEFAULT_WIKI_GUIDED_RATIO)

    def test_env_clamped_to_unit_interval(self):
        # / ratios outside [0, 1] clamp to boundary
        with patch.dict(os.environ, {"WIKI_GUIDED_RATIO": "1.5"}):
            engine = EvolutionEngine(rng=np.random.default_rng(42))
        assert engine._wiki_guided_ratio == 1.0

        with patch.dict(os.environ, {"WIKI_GUIDED_RATIO": "-0.5"}):
            engine = EvolutionEngine(rng=np.random.default_rng(42))
        assert engine._wiki_guided_ratio == 0.0


# ──────────────────────────────────────────────────────
# _mutate_killed integration: ctx tokens stored
# ──────────────────────────────────────────────────────

class TestMutateKilledRow:
    @pytest.mark.asyncio
    async def test_ctx_tokens_stored_equals_ctx_len_div_4(self):
        # / the chars-per-token estimate is len//4
        engine = EvolutionEngine(rng=np.random.default_rng(0))
        engine._generation = 7
        # / force guided path
        engine._wiki_guided_ratio = 1.0

        killed_config = _minimal_config()
        killed = [{"id": "sid", "config": killed_config, "reason": "bottom quartile"}]

        ctx_text = "x" * 1000  # / 250 tokens

        from src.strategies.strategy_pool import StrategyPool
        sp = StrategyPool()
        summary = {"killed": [], "mutated": [], "errors": []}

        mock_pool = MagicMock()
        captured_tokens: list[int] = []

        async def _fake_store(pool, generation, parent_strategy_id, wiki_guided, wiki_context_tokens):
            captured_tokens.append(wiki_context_tokens)
            return 99

        # / wiki_context returns ctx, top performer returns empty
        mock_wc_instance = MagicMock()
        mock_wc_instance.get_mutation_context = AsyncMock(return_value=ctx_text)

        async def _fake_mutate(*a, **kw):
            # / return a valid mutated config dict
            return [{"id": "mut_x", "parent_id": "sid"}]

        with (
            patch("src.evolution.evolution_engine.WikiContext", return_value=mock_wc_instance),
            patch("src.evolution.evolution_engine.fetch_recent_trades",
                  new=AsyncMock(return_value=[])),
            patch("src.evolution.evolution_engine.store_evolution_mutation", side_effect=_fake_store),
            patch("src.evolution.evolution_engine.update_evolution_mutation_outcome",
                  new=AsyncMock()),
            patch("src.evolution.evolution_engine.mutate_strategy", side_effect=_fake_mutate),
        ):
            await engine._mutate_killed(mock_pool, killed, sp, summary)

        # / ctx is 1000 chars, tokens = 1000 // 4 = 250
        assert captured_tokens == [250]

    @pytest.mark.asyncio
    async def test_unguided_path_ctx_tokens_zero(self):
        # / when wiki_guided=False, ctx=None and tokens=0
        engine = EvolutionEngine(rng=np.random.default_rng(0))
        engine._generation = 1
        engine._wiki_guided_ratio = 0.0  # / force unguided

        killed = [{"id": "sid", "config": _minimal_config(), "reason": "x"}]
        from src.strategies.strategy_pool import StrategyPool
        sp = StrategyPool()
        summary = {"killed": [], "mutated": [], "errors": []}

        mock_pool = MagicMock()
        captured: list[tuple[bool, int]] = []

        async def _fake_store(pool, generation, parent_strategy_id, wiki_guided, wiki_context_tokens):
            captured.append((wiki_guided, wiki_context_tokens))
            return 1

        async def _fake_mutate(*a, **kw):
            # / must return list of config dicts
            return [{"id": "m", "parent_id": "sid"}]

        with (
            patch("src.evolution.evolution_engine.WikiContext") as mock_wc_cls,
            patch("src.evolution.evolution_engine.fetch_recent_trades",
                  new=AsyncMock(return_value=[])),
            patch("src.evolution.evolution_engine.store_evolution_mutation", side_effect=_fake_store),
            patch("src.evolution.evolution_engine.update_evolution_mutation_outcome",
                  new=AsyncMock()),
            patch("src.evolution.evolution_engine.mutate_strategy", side_effect=_fake_mutate),
        ):
            mock_wc_cls.return_value.get_mutation_context = AsyncMock(return_value="should_not_use")
            await engine._mutate_killed(mock_pool, killed, sp, summary)

        assert captured == [(False, 0)]
