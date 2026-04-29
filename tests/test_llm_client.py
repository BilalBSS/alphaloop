# / tests for shared llm http client (groq + deepseek)

from __future__ import annotations

import random
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import src.data.llm_client as mod


@pytest.fixture(autouse=True)
def _reset_clients():
    # / clean module-level state before and after each test
    mod._clients.clear()
    yield
    mod._clients.clear()


class TestGetLlmClient:
    @pytest.mark.asyncio
    async def test_groq_returns_client(self):
        client = await mod.get_llm_client("groq")
        assert isinstance(client, httpx.AsyncClient)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_deepseek_returns_client(self):
        client = await mod.get_llm_client("deepseek")
        assert isinstance(client, httpx.AsyncClient)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_separate_clients_per_provider(self):
        g = await mod.get_llm_client("groq")
        d = await mod.get_llm_client("deepseek")
        assert g is not d
        await g.aclose()
        await d.aclose()

    @pytest.mark.asyncio
    async def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="unknown llm provider"):
            await mod.get_llm_client("openai")

    @pytest.mark.asyncio
    async def test_recreates_client_if_closed(self):
        c1 = await mod.get_llm_client("groq")
        await c1.aclose()
        c2 = await mod.get_llm_client("groq")
        assert c2 is not c1
        assert not c2.is_closed
        await c2.aclose()


class TestCloseLlmClients:
    @pytest.mark.asyncio
    async def test_closes_all_and_clears(self):
        g = await mod.get_llm_client("groq")
        d = await mod.get_llm_client("deepseek")
        await mod.close_llm_clients()
        assert g.is_closed
        assert d.is_closed
        assert len(mod._clients) == 0

    @pytest.mark.asyncio
    async def test_noop_when_empty(self):
        # / should not raise when nothing to close
        await mod.close_llm_clients()
        assert len(mod._clients) == 0


class TestLlmCall:
    @pytest.mark.asyncio
    async def test_valid_call_makes_correct_request(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.is_closed = False

        with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            with patch.object(mod, "get_llm_client", return_value=mock_client):
                result = await mod.llm_call("groq", [{"role": "user", "content": "hi"}])

        assert result == {"choices": [{"message": {"content": "ok"}}]}
        call_kwargs = mock_client.post.call_args
        assert "chat/completions" in call_kwargs[0][0]
        assert call_kwargs[1]["headers"]["Authorization"] == "Bearer test-key"
        assert call_kwargs[1]["json"]["messages"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self):
        with patch.dict("os.environ", {}, clear=True), pytest.raises(ValueError, match="missing GROQ_API_KEY"):
            await mod.llm_call("groq", [{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="unknown llm provider"):
            await mod.llm_call("openai", [{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_forwards_model_param(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": []}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.is_closed = False

        with patch.dict("os.environ", {"GROQ_API_KEY": "k"}):
            with patch.object(mod, "get_llm_client", return_value=mock_client):
                await mod.llm_call(
                    "groq",
                    [{"role": "user", "content": "x"}],
                    model="llama-3.3-70b-versatile",
                )

        payload = mock_client.post.call_args[1]["json"]
        assert payload["model"] == "llama-3.3-70b-versatile"

    @pytest.mark.asyncio
    async def test_forwards_timeout_param(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": []}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.is_closed = False

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "k"}):
            with patch.object(mod, "get_llm_client", return_value=mock_client):
                await mod.llm_call(
                    "deepseek",
                    [{"role": "user", "content": "x"}],
                    timeout=60.0,
                )

        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["timeout"] == 60.0

    @pytest.mark.asyncio
    async def test_uses_default_timeout_when_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": []}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.is_closed = False

        with patch.dict("os.environ", {"GROQ_API_KEY": "k"}):
            with patch.object(mod, "get_llm_client", return_value=mock_client):
                await mod.llm_call("groq", [{"role": "user", "content": "x"}])

        call_kwargs = mock_client.post.call_args[1]
        # / groq default timeout is 15.0
        assert call_kwargs["timeout"] == 15.0


class TestParseProviderSplit:
    def test_default_format_parses(self):
        result = mod._parse_provider_split("groq:0.9,cerebras:0.1")
        assert result == [("groq", 0.9), ("cerebras", 0.1)]

    def test_weights_normalize_to_one(self):
        # / weights 9 and 1 should normalize to 0.9 and 0.1
        result = mod._parse_provider_split("groq:9,cerebras:1")
        assert result == [("groq", 0.9), ("cerebras", 0.1)]
        assert sum(w for _, w in result) == pytest.approx(1.0)

    def test_unnormalized_fractions_normalize(self):
        # / 0.4 and 0.6 already sum to 1.0 but exercise the divide-by-total branch
        result = mod._parse_provider_split("groq:0.4,cerebras:0.6")
        assert result[0] == ("groq", pytest.approx(0.4))
        assert result[1] == ("cerebras", pytest.approx(0.6))

    def test_empty_string_defaults_to_pure_groq(self):
        assert mod._parse_provider_split("") == [("groq", 1.0)]

    def test_none_defaults_to_pure_groq(self):
        assert mod._parse_provider_split(None) == [("groq", 1.0)]

    def test_whitespace_only_defaults_to_pure_groq(self):
        assert mod._parse_provider_split("   ") == [("groq", 1.0)]

    def test_single_provider_passes_through(self):
        result = mod._parse_provider_split("groq:1.0")
        assert result == [("groq", 1.0)]

    def test_single_cerebras_normalizes(self):
        # / single entry with any positive weight normalizes to 1.0
        result = mod._parse_provider_split("cerebras:5")
        assert result == [("cerebras", 1.0)]

    def test_unknown_provider_skipped(self):
        # / openai is not in _FALLBACK_PROVIDERS — dropped; remainder normalized
        result = mod._parse_provider_split("groq:0.9,openai:0.1")
        assert result == [("groq", 1.0)]

    def test_all_unknown_falls_back_to_default(self):
        result = mod._parse_provider_split("openai:1.0,anthropic:0.5")
        assert result == [("groq", 1.0)]

    def test_malformed_entry_skipped(self):
        # / no colon means entry is invalid; the valid pair still applies
        result = mod._parse_provider_split("groq,cerebras:1.0")
        assert result == [("cerebras", 1.0)]

    def test_non_numeric_weight_skipped(self):
        result = mod._parse_provider_split("groq:abc,cerebras:1.0")
        assert result == [("cerebras", 1.0)]

    def test_negative_weight_skipped(self):
        result = mod._parse_provider_split("groq:-0.5,cerebras:1.0")
        assert result == [("cerebras", 1.0)]

    def test_zero_total_falls_back_to_default(self):
        result = mod._parse_provider_split("groq:0,cerebras:0")
        assert result == [("groq", 1.0)]

    def test_whitespace_in_entries_tolerated(self):
        result = mod._parse_provider_split(" groq : 0.9 , cerebras : 0.1 ")
        assert result == [("groq", 0.9), ("cerebras", 0.1)]

    def test_case_insensitive_provider_name(self):
        result = mod._parse_provider_split("GROQ:0.5,CEREBRAS:0.5")
        assert result == [("groq", 0.5), ("cerebras", 0.5)]


class TestReloadProviderSplit:
    def test_reload_picks_up_env_change(self):
        with patch.dict("os.environ", {"LLM_PROVIDER_SPLIT": "cerebras:1.0"}):
            result = mod.reload_provider_split()
        assert result == [("cerebras", 1.0)]
        assert mod._PROVIDER_SPLIT == [("cerebras", 1.0)]

    def test_reload_empty_env_uses_default(self):
        # / missing env => default split of groq:0.9,cerebras:0.1
        with patch.dict("os.environ", {}, clear=True):
            result = mod.reload_provider_split()
        assert result[0][0] == "groq"
        assert result[1][0] == "cerebras"
        assert result[0][1] == pytest.approx(0.9)
        assert result[1][1] == pytest.approx(0.1)


@pytest.fixture
def _restore_split():
    # / snapshot + restore module-level split so tests can mutate freely
    snapshot = list(mod._PROVIDER_SPLIT)
    yield
    mod._PROVIDER_SPLIT = snapshot


class TestPickPrimaryProvider:
    def test_single_provider_always_returns_it(self, _restore_split):
        mod._PROVIDER_SPLIT = [("groq", 1.0)]
        for _ in range(100):
            assert mod._pick_primary_provider() == "groq"

    def test_single_cerebras_always_returns_it(self, _restore_split):
        mod._PROVIDER_SPLIT = [("cerebras", 1.0)]
        for _ in range(100):
            assert mod._pick_primary_provider() == "cerebras"

    def test_weighted_distribution_approximates_weights(self, _restore_split):
        # / 10000 rolls of 0.9/0.1 should give ~9000/1000 groq/cerebras
        # / binomial std = sqrt(n*p*(1-p)) = sqrt(10000*0.9*0.1) = 30; 3sigma ~90, use 200 for safety
        mod._PROVIDER_SPLIT = [("groq", 0.9), ("cerebras", 0.1)]
        random.seed(12345)
        counts = {"groq": 0, "cerebras": 0}
        for _ in range(10000):
            counts[mod._pick_primary_provider()] += 1
        assert abs(counts["groq"] - 9000) < 200
        assert abs(counts["cerebras"] - 1000) < 200
        assert counts["groq"] + counts["cerebras"] == 10000

    def test_fifty_fifty_distribution(self, _restore_split):
        mod._PROVIDER_SPLIT = [("groq", 0.5), ("cerebras", 0.5)]
        random.seed(42)
        counts = {"groq": 0, "cerebras": 0}
        for _ in range(10000):
            counts[mod._pick_primary_provider()] += 1
        # / 3sigma ~150 for 50/50 at n=10000, use 300 for safety
        assert abs(counts["groq"] - 5000) < 300
        assert abs(counts["cerebras"] - 5000) < 300

    def test_deterministic_with_seed(self, _restore_split):
        mod._PROVIDER_SPLIT = [("groq", 0.9), ("cerebras", 0.1)]
        random.seed(7)
        seq1 = [mod._pick_primary_provider() for _ in range(20)]
        random.seed(7)
        seq2 = [mod._pick_primary_provider() for _ in range(20)]
        assert seq1 == seq2


class TestBuildFallbackChain:
    def test_groq_primary_order(self, _restore_split):
        mod._PROVIDER_SPLIT = [("groq", 1.0)]
        chain = mod.build_fallback_chain(
            groq_fast="g70", cerebras_fast="c70",
            groq_slow="g120", cerebras_slow="c120",
        )
        assert chain == [
            ("groq", "g70"),
            ("cerebras", "c70"),
            ("groq", "g120"),
            ("cerebras", "c120"),
        ]

    def test_cerebras_primary_order(self, _restore_split):
        mod._PROVIDER_SPLIT = [("cerebras", 1.0)]
        chain = mod.build_fallback_chain(
            groq_fast="g70", cerebras_fast="c70",
            groq_slow="g120", cerebras_slow="c120",
        )
        assert chain == [
            ("cerebras", "c70"),
            ("groq", "g70"),
            ("cerebras", "c120"),
            ("groq", "g120"),
        ]

    def test_chain_length_always_four(self, _restore_split):
        for split in ([("groq", 1.0)], [("cerebras", 1.0)], [("groq", 0.5), ("cerebras", 0.5)]):
            mod._PROVIDER_SPLIT = split
            random.seed(1)
            chain = mod.build_fallback_chain(
                groq_fast="a", cerebras_fast="b",
                groq_slow="c", cerebras_slow="d",
            )
            assert len(chain) == 4

    def test_chain_contains_all_four_models_regardless_of_primary(self, _restore_split):
        # / both primary-orderings must include every (provider, model) slot exactly once
        for primary in ("groq", "cerebras"):
            mod._PROVIDER_SPLIT = [(primary, 1.0)]
            chain = mod.build_fallback_chain(
                groq_fast="g70", cerebras_fast="c70",
                groq_slow="g120", cerebras_slow="c120",
            )
            assert set(chain) == {
                ("groq", "g70"), ("cerebras", "c70"),
                ("groq", "g120"), ("cerebras", "c120"),
            }

    def test_cerebras_branch_fires_when_rolled(self, _restore_split):
        # / integration: patch _pick_primary_provider and verify chain swaps accordingly
        mod._PROVIDER_SPLIT = [("groq", 0.9), ("cerebras", 0.1)]
        with patch.object(mod, "_pick_primary_provider", return_value="cerebras"):
            chain = mod.build_fallback_chain(
                groq_fast="g70", cerebras_fast="c70",
                groq_slow="g120", cerebras_slow="c120",
            )
        assert chain[0] == ("cerebras", "c70")
        assert chain[1] == ("groq", "g70")

    def test_groq_branch_fires_when_rolled(self, _restore_split):
        mod._PROVIDER_SPLIT = [("groq", 0.9), ("cerebras", 0.1)]
        with patch.object(mod, "_pick_primary_provider", return_value="groq"):
            chain = mod.build_fallback_chain(
                groq_fast="g70", cerebras_fast="c70",
                groq_slow="g120", cerebras_slow="c120",
            )
        assert chain[0] == ("groq", "g70")
        assert chain[1] == ("cerebras", "c70")
