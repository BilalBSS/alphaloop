# / tests for OllamaEmbedder — 768-dim vectors, failure returns None, batch handling

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.knowledge.embedder import EMBED_DIM, OllamaEmbedder


def _make_response(status_code: int = 200, json_body: dict | None = None) -> MagicMock:
    # / build a minimal httpx.Response-like mock that satisfies .json() + .raise_for_status()
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = RuntimeError(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _mock_async_client(resp: MagicMock | None = None, exc: Exception | None = None) -> MagicMock:
    client = MagicMock()
    client.is_closed = False
    if exc is not None:
        client.post = AsyncMock(side_effect=exc)
    else:
        client.post = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()
    return client


# ──────────────────────────────────────────────────────
# constants
# ──────────────────────────────────────────────────────

def test_embed_dim_is_768():
    # / nomic-embed-text produces 768-dim vectors; callers depend on this exactly
    assert EMBED_DIM == 768


# ──────────────────────────────────────────────────────
# base_url resolution
# ──────────────────────────────────────────────────────

def test_default_base_url_from_env():
    with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://my-ollama:9999"}):
        emb = OllamaEmbedder()
    assert emb._base_url == "http://my-ollama:9999"


def test_default_base_url_when_env_unset():
    env = {k: v for k, v in os.environ.items() if k != "OLLAMA_BASE_URL"}
    with patch.dict(os.environ, env, clear=True):
        emb = OllamaEmbedder()
    assert emb._base_url == "http://localhost:11434"


def test_custom_base_url_override_trailing_slash_stripped():
    emb = OllamaEmbedder(base_url="http://custom:1234/")
    assert emb._base_url == "http://custom:1234"


# ──────────────────────────────────────────────────────
# embed()
# ──────────────────────────────────────────────────────

class TestEmbed:
    @pytest.mark.asyncio
    async def test_success_returns_768_dim_list(self):
        # / happy path — /api/embed returns {"embeddings": [[...]]}
        body = {"embeddings": [[0.1] * EMBED_DIM]}
        client = _mock_async_client(_make_response(200, body))
        emb = OllamaEmbedder(base_url="http://localhost:11434")
        with patch.object(emb, "_get_client", AsyncMock(return_value=client)):
            vec = await emb.embed("test text")

        assert vec is not None
        assert isinstance(vec, list)
        assert len(vec) == EMBED_DIM
        assert all(isinstance(v, float) for v in vec)

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        # / network exception must not propagate — callers skip embed on None
        client = _mock_async_client(exc=RuntimeError("connection refused"))
        emb = OllamaEmbedder()
        with patch.object(emb, "_get_client", AsyncMock(return_value=client)):
            vec = await emb.embed("hello world")
        assert vec is None

    @pytest.mark.asyncio
    async def test_http_status_error_returns_none(self):
        # / 500 response → raise_for_status raises → return None
        client = _mock_async_client(_make_response(500, {}))
        emb = OllamaEmbedder()
        with patch.object(emb, "_get_client", AsyncMock(return_value=client)):
            vec = await emb.embed("hello world")
        assert vec is None

    @pytest.mark.asyncio
    async def test_empty_text_returns_none_without_http_call(self):
        # / empty text short-circuits — never touches the network
        client = _mock_async_client(_make_response(200, {"embeddings": [[0.0] * EMBED_DIM]}))
        emb = OllamaEmbedder()
        with patch.object(emb, "_get_client", AsyncMock(return_value=client)):
            assert await emb.embed("") is None
            assert await emb.embed("   ") is None
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_bad_shape_returns_none(self):
        # / if ollama returns wrong-length vector, treat as failure
        body = {"embeddings": [[0.1] * 500]}  # / not 768
        client = _mock_async_client(_make_response(200, body))
        emb = OllamaEmbedder()
        with patch.object(emb, "_get_client", AsyncMock(return_value=client)):
            vec = await emb.embed("text")
        assert vec is None

    @pytest.mark.asyncio
    async def test_missing_embedding_key_returns_none(self):
        # / malformed response — missing embedding key
        client = _mock_async_client(_make_response(200, {"error": "model not found"}))
        emb = OllamaEmbedder()
        with patch.object(emb, "_get_client", AsyncMock(return_value=client)):
            vec = await emb.embed("text")
        assert vec is None


# ──────────────────────────────────────────────────────
# embed_batch()
# ──────────────────────────────────────────────────────

class TestEmbedBatch:
    @pytest.mark.asyncio
    async def test_preserves_order_and_length(self):
        # / batch hits /api/embed once per batch_size chunk; positions must align with input
        texts = ["alpha", "beta", "gamma", "delta"]
        # / first http call: inputs "alpha","beta"; second: "gamma","delta"
        # / mock returns an embedding per input with the input index encoded in [0]
        responses = [
            _make_response(200, {"embeddings": [[0.0] + [0.0] * (EMBED_DIM - 1),
                                                [1.0] + [0.0] * (EMBED_DIM - 1)]}),
            _make_response(200, {"embeddings": [[2.0] + [0.0] * (EMBED_DIM - 1),
                                                [3.0] + [0.0] * (EMBED_DIM - 1)]}),
        ]
        client = MagicMock()
        client.post = AsyncMock(side_effect=responses)

        emb = OllamaEmbedder()
        with patch.object(emb, "_get_client", AsyncMock(return_value=client)):
            results = await emb.embed_batch(texts, batch_size=2)

        assert len(results) == len(texts)
        for i, r in enumerate(results):
            assert r is not None
            assert r[0] == float(i)
        assert client.post.await_count == 2  # / two batches, one http call each

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty_list(self):
        emb = OllamaEmbedder()
        out = await emb.embed_batch([])
        assert out == []

    @pytest.mark.asyncio
    async def test_batch_http_failure_leaves_nones(self):
        # / if the http call for a batch raises, that batch's items remain None; other batches ok
        ok_response = _make_response(
            200, {"embeddings": [[0.1] * EMBED_DIM, [0.2] * EMBED_DIM]}
        )
        client = MagicMock()
        client.post = AsyncMock(side_effect=[RuntimeError("boom"), ok_response])

        emb = OllamaEmbedder()
        with patch.object(emb, "_get_client", AsyncMock(return_value=client)):
            results = await emb.embed_batch(["a", "b", "c", "d"], batch_size=2)

        assert len(results) == 4
        # / first batch failed → items 0, 1 are None; second batch ok → items 2, 3 are lists
        assert results[0] is None
        assert results[1] is None
        assert results[2] is not None
        assert results[3] is not None

    @pytest.mark.asyncio
    async def test_shape_mismatch_skips_batch(self):
        # / if ollama returns fewer embeddings than inputs, that batch's items stay None
        mismatched = _make_response(200, {"embeddings": [[0.1] * EMBED_DIM]})
        client = MagicMock()
        client.post = AsyncMock(return_value=mismatched)

        emb = OllamaEmbedder()
        with patch.object(emb, "_get_client", AsyncMock(return_value=client)):
            results = await emb.embed_batch(["a", "b"], batch_size=2)

        assert results == [None, None]
