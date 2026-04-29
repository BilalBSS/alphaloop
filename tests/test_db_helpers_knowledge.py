# / tests for src/knowledge/db_helpers.py — evolution_mutations + post_mortems + regime_shifts rows

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.knowledge.db_helpers import (
    store_evolution_mutation,
    store_post_mortem_row,
    store_regime_shift_row,
    update_evolution_mutation_by_mutant,
    update_evolution_mutation_outcome,
)


def _mock_pool(fetchrow_return=None):
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    mock_conn.execute = AsyncMock(return_value="UPDATE 1")
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool, mock_conn


# ──────────────────────────────────────────────────────
# store_evolution_mutation
# ──────────────────────────────────────────────────────

class TestStoreEvolutionMutation:
    @pytest.mark.asyncio
    async def test_returns_row_id(self):
        pool, _ = _mock_pool(fetchrow_return={"id": 77})
        n = await store_evolution_mutation(
            pool, generation=3, parent_strategy_id="sid_parent",
            wiki_guided=True, wiki_context_tokens=250,
        )
        assert n == 77

    @pytest.mark.asyncio
    async def test_fields_passed_correctly(self):
        pool, conn = _mock_pool(fetchrow_return={"id": 1})
        await store_evolution_mutation(
            pool, generation=5, parent_strategy_id="sid_p",
            wiki_guided=False, wiki_context_tokens=0,
        )
        args = conn.fetchrow.await_args.args
        assert "INSERT INTO evolution_mutations" in args[0]
        assert args[1] == 5
        assert args[2] == "sid_p"
        assert args[3] is False
        assert args[4] == 0


# ──────────────────────────────────────────────────────
# update_evolution_mutation_outcome
# ──────────────────────────────────────────────────────

class TestUpdateEvolutionMutationOutcome:
    @pytest.mark.asyncio
    async def test_no_fields_skips_db_call(self):
        # / passing only row_id (no fields) should be a no-op
        pool, conn = _mock_pool()
        await update_evolution_mutation_outcome(pool, row_id=1)
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_mutant_id_only(self):
        pool, conn = _mock_pool()
        await update_evolution_mutation_outcome(pool, row_id=1, mutant_strategy_id="mut_x")
        sql = conn.execute.await_args.args[0]
        assert "UPDATE evolution_mutations SET" in sql
        assert "mutant_strategy_id" in sql
        # / WHERE id = $N where N is the last param
        assert conn.execute.await_args.args[-1] == 1

    @pytest.mark.asyncio
    async def test_update_sharpe_fields_as_decimal(self):
        pool, conn = _mock_pool()
        await update_evolution_mutation_outcome(
            pool, row_id=5,
            parent_sharpe=1.25, mutant_sharpe=1.80, sharpe_delta=0.55,
        )
        args = conn.execute.await_args.args
        # / check decimal conversion happened (Decimal type appears among args)
        assert any(isinstance(a, Decimal) for a in args)
        sql = args[0]
        assert "parent_sharpe" in sql
        assert "mutant_sharpe" in sql
        assert "sharpe_delta" in sql

    @pytest.mark.asyncio
    async def test_update_survived_flag(self):
        pool, conn = _mock_pool()
        await update_evolution_mutation_outcome(pool, row_id=9, survived=True)
        sql = conn.execute.await_args.args[0]
        assert "survived" in sql
        # / the bool must appear in params
        assert True in conn.execute.await_args.args

    @pytest.mark.asyncio
    async def test_mutation_diff_dict_passed_through(self):
        pool, conn = _mock_pool()
        diff = {"changed": ["rsi.period"], "from": 14, "to": 21}
        await update_evolution_mutation_outcome(pool, row_id=1, mutation_diff=diff)
        args = conn.execute.await_args.args
        assert diff in args  # / the dict should be one of the params (jsonb codec)


# ──────────────────────────────────────────────────────
# update_evolution_mutation_by_mutant
# ──────────────────────────────────────────────────────

class TestUpdateByMutant:
    @pytest.mark.asyncio
    async def test_empty_mutant_id_skipped(self):
        pool, conn = _mock_pool()
        await update_evolution_mutation_by_mutant(pool, mutant_strategy_id="")
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_fields_skips_db_call(self):
        pool, conn = _mock_pool()
        await update_evolution_mutation_by_mutant(pool, mutant_strategy_id="mut_x")
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_by_mutant_id(self):
        pool, conn = _mock_pool()
        await update_evolution_mutation_by_mutant(
            pool, mutant_strategy_id="mut_x", survived=True,
        )
        args = conn.execute.await_args.args
        sql = args[0]
        assert "WHERE mutant_strategy_id = " in sql
        # / last positional arg is the mutant id
        assert args[-1] == "mut_x"

    @pytest.mark.asyncio
    async def test_idempotent_same_mutant_update_safe(self):
        # / repeated calls for the same mutant should all succeed (no unique constraint)
        pool, conn = _mock_pool()
        for _ in range(3):
            await update_evolution_mutation_by_mutant(
                pool, mutant_strategy_id="mut_x", survived=False,
            )
        assert conn.execute.await_count == 3


# ──────────────────────────────────────────────────────
# store_post_mortem_row
# ──────────────────────────────────────────────────────

class TestStorePostMortemRow:
    @pytest.mark.asyncio
    async def test_returns_id(self):
        pool, _ = _mock_pool(fetchrow_return={"id": 42})
        n = await store_post_mortem_row(
            pool, strategy_id="sid", symbol="AAPL",
            trigger_type="loss_threshold", pnl=-100.0,
            expected_pnl=None, deviation_sigma=None,
            details={"trade_id": 5}, wiki_path="post-mortems/x.md",
        )
        assert n == 42

    @pytest.mark.asyncio
    async def test_decimal_conversion(self):
        pool, conn = _mock_pool(fetchrow_return={"id": 1})
        await store_post_mortem_row(
            pool, strategy_id="s", symbol="A",
            trigger_type="t", pnl=-50.25,
            expected_pnl=-40.0, deviation_sigma=1.5,
            details={"k": 1}, wiki_path="p",
        )
        args = conn.fetchrow.await_args.args
        # / pnl, expected_pnl, deviation_sigma all become Decimal
        assert Decimal("-50.25") in args
        assert Decimal("-40.0") in args
        assert Decimal("1.5") in args

    @pytest.mark.asyncio
    async def test_details_jsonb_dict_passed_through(self):
        pool, conn = _mock_pool(fetchrow_return={"id": 1})
        details = {"trade_id": 7, "model_used": "llama", "nested": {"a": 1}}
        await store_post_mortem_row(
            pool, strategy_id="s", symbol="A", trigger_type="t",
            pnl=None, expected_pnl=None, deviation_sigma=None,
            details=details, wiki_path=None,
        )
        args = conn.fetchrow.await_args.args
        assert details in args

    @pytest.mark.asyncio
    async def test_none_details_becomes_none(self):
        # / empty dict → falsy → None is stored
        pool, conn = _mock_pool(fetchrow_return={"id": 1})
        await store_post_mortem_row(
            pool, strategy_id="s", symbol="A", trigger_type="t",
            pnl=None, expected_pnl=None, deviation_sigma=None,
            details={}, wiki_path=None,
        )
        args = conn.fetchrow.await_args.args
        # / falsy dict -> None in the params
        assert None in args


# ──────────────────────────────────────────────────────
# store_regime_shift_row
# ──────────────────────────────────────────────────────

class TestStoreRegimeShiftRow:
    @pytest.mark.asyncio
    async def test_returns_id(self):
        pool, _ = _mock_pool(fetchrow_return={"id": 9})
        n = await store_regime_shift_row(
            pool, old_regime="bull", new_regime="bear",
            market="equity", confidence=0.88, wiki_path="r.md",
        )
        assert n == 9

    @pytest.mark.asyncio
    async def test_confidence_as_decimal(self):
        pool, conn = _mock_pool(fetchrow_return={"id": 1})
        await store_regime_shift_row(
            pool, "bull", "bear", "equity", 0.85, "r.md",
        )
        args = conn.fetchrow.await_args.args
        assert Decimal("0.85") in args

    @pytest.mark.asyncio
    async def test_none_confidence_stored_as_none(self):
        pool, conn = _mock_pool(fetchrow_return={"id": 1})
        await store_regime_shift_row(
            pool, "bull", "bear", "equity", None, "r.md",
        )
        args = conn.fetchrow.await_args.args
        # / None confidence must pass through unchanged
        assert None in args
