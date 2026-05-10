
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.evolution import lesson_distiller as ld


def _llm_response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


# ──────────────────────────────────────────────────────
# prompt builders
# ──────────────────────────────────────────────────────


class TestBuildKillPrompt:
    def test_includes_core_fields(self):
        p = ld.build_kill_prompt(
            strategy_id="strategy_022", config={"name": "rsi-mean-rev", "sector": "tech"},
            sharpe=-1.2, trade_count=12, composite=0.05,
            days_alive=8, reason="bottom quartile (composite=0.0500)",
            recent_trades=[{"symbol": "AAPL", "side": "buy", "pnl": -10}],
            regime="bull",
        )
        assert "strategy_022" in p
        assert "bottom quartile" in p
        assert "-1.200" in p or "-1.2" in p
        assert "rsi-mean-rev" in p
        assert "bull" in p
        assert "Task" in p

    def test_no_recent_trades_still_renders(self):
        p = ld.build_kill_prompt(
            strategy_id="s1", config=None, sharpe=None, trade_count=None,
            composite=None, days_alive=None, reason="dormant",
            recent_trades=None, regime=None,
        )
        assert "s1" in p
        assert "dormant" in p
        assert "Task" in p


class TestBuildPromotionPrompt:
    def test_includes_core_fields(self):
        p = ld.build_promotion_prompt(
            strategy_id="strategy_007", config={"sector": "energy"},
            sharpe=1.8, win_rate=0.62, paper_days=14, trade_count=30,
            recent_trades=None, regime="bear",
        )
        assert "strategy_007" in p
        assert "1.800" in p
        assert "62.00%" in p
        assert "14" in p
        assert "energy" in p
        assert "bear" in p


class TestBuildMutationPrompt:
    def test_diff_oriented_framing(self):
        p = ld.build_mutation_prompt(
            parent_id="p1", mutant_id="m1",
            parent_config={"name": "p"}, mutant_config={"name": "m"},
            parent_sharpe=0.8, mutant_sharpe=1.1,
            composite=0.65, sharpe_delta=0.3, wiki_guided=True,
        )
        assert "p1" in p and "m1" in p
        assert "Wiki-guided: True" in p
        assert "+0.300" in p
        assert "Parent Config" in p
        assert "Mutant Config" in p


# ──────────────────────────────────────────────────────
# distill_kill
# ──────────────────────────────────────────────────────


class TestDistillKill:
    @pytest.mark.asyncio
    async def test_returns_llm_content_on_success(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "k")
        with patch.object(ld, "llm_call", new=AsyncMock(return_value=_llm_response("LLM lesson"))):
            out = await ld.distill_kill(
                strategy_id="s", config={"name": "x"}, sharpe=-0.5,
                trade_count=12, composite=0.01, days_alive=10,
                reason="bottom quartile",
            )
        assert out == "LLM lesson"

    @pytest.mark.asyncio
    async def test_falls_back_to_template_when_llm_fails(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "k")
        with patch.object(ld, "llm_call", new=AsyncMock(side_effect=RuntimeError("502"))):
            out = await ld.distill_kill(
                strategy_id="s_99", config=None, sharpe=-1.0,
                trade_count=5, composite=0.0, days_alive=3,
                reason="dormant", regime="bull",
            )
        assert "s_99" in out
        assert "dormant" in out
        assert "bull" in out

    @pytest.mark.asyncio
    async def test_template_when_no_api_keys(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
        with patch.object(ld, "llm_call", new=AsyncMock()) as m:
            out = await ld.distill_kill(
                strategy_id="s", config=None, sharpe=None, trade_count=None,
                composite=None, days_alive=None, reason="r",
            )
        assert m.call_count == 0
        assert "s" in out and "r" in out

    @pytest.mark.asyncio
    async def test_empty_llm_choices_falls_back(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "k")
        with patch.object(ld, "llm_call", new=AsyncMock(return_value={"choices": []})):
            out = await ld.distill_kill(
                strategy_id="s", config=None, sharpe=None, trade_count=None,
                composite=None, days_alive=None, reason="x",
            )
        assert "s" in out


# ──────────────────────────────────────────────────────
# distill_promotion
# ──────────────────────────────────────────────────────


class TestDistillPromotion:
    @pytest.mark.asyncio
    async def test_returns_llm_content_on_success(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "k")
        with patch.object(ld, "llm_call", new=AsyncMock(return_value=_llm_response("Promotion insight"))):
            out = await ld.distill_promotion(
                strategy_id="s_p", config={"sector": "tech"},
                sharpe=1.5, win_rate=0.65, paper_days=10,
            )
        assert out == "Promotion insight"

    @pytest.mark.asyncio
    async def test_template_format_on_failure(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "k")
        with patch.object(ld, "llm_call", new=AsyncMock(side_effect=Exception("timeout"))):
            out = await ld.distill_promotion(
                strategy_id="sid_x", config=None, sharpe=2.0,
                win_rate=0.7, paper_days=14,
            )
        assert "sid_x" in out and "promoted" in out and "14" in out


# ──────────────────────────────────────────────────────
# distill_mutation
# ──────────────────────────────────────────────────────


class TestDistillMutation:
    @pytest.mark.asyncio
    async def test_returns_llm_content_on_success(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "k")
        with patch.object(ld, "llm_call", new=AsyncMock(return_value=_llm_response("Mutation insight"))):
            out = await ld.distill_mutation(
                parent_id="p", mutant_id="m",
                parent_config=None, mutant_config=None,
                parent_sharpe=0.5, mutant_sharpe=0.7,
                composite=0.4, sharpe_delta=0.2, wiki_guided=False,
            )
        assert out == "Mutation insight"

    @pytest.mark.asyncio
    async def test_template_describes_direction(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "k")
        with patch.object(ld, "llm_call", new=AsyncMock(side_effect=ValueError("bad"))):
            out = await ld.distill_mutation(
                parent_id="p1", mutant_id="m1",
                parent_config=None, mutant_config=None,
                parent_sharpe=1.0, mutant_sharpe=0.4,
                composite=0.2, sharpe_delta=-0.6, wiki_guided=True,
            )
        assert "m1" in out
        assert "regression" in out
        assert "True" in out
