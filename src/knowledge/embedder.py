# / ollama nomic-embed-text client for local, free embeddings
# / failure returns None — callers skip embed and backfill later

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

# / nomic-embed-text produces 768-dim vectors
EMBED_DIM = 768
DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_TIMEOUT = 5.0


class OllamaEmbedder:
    # / local ollama embeddings via http

    def __init__(
        self,
        base_url: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self._base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self._model = model
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        # / lazy-init shared httpx client
        async with self._client_lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(timeout=self._timeout)
            return self._client

    async def close(self) -> None:
        # / shutdown hook
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def embed(self, text: str) -> list[float] | None:
        # / embed a single string via ollama /api/embed, returns None on any failure
        if not text or not text.strip():
            return None
        try:
            client = await self._get_client()
            resp = await client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": text},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            # / /api/embed returns {"embeddings": [[...]]}; take the first vector
            vecs = data.get("embeddings")
            if not isinstance(vecs, list) or not vecs:
                logger.warning("ollama_embed_empty_response", keys=list(data.keys()))
                return None
            vec = vecs[0]
            if not isinstance(vec, list) or len(vec) != EMBED_DIM:
                logger.warning(
                    "ollama_embed_bad_shape",
                    length=len(vec) if isinstance(vec, list) else None,
                    expected=EMBED_DIM,
                )
                return None
            return [float(v) for v in vec]
        except Exception as exc:
            logger.info("ollama_embed_failed", error=str(exc)[:120])
            return None

    async def embed_batch(
        self, texts: list[str], batch_size: int = 8,
    ) -> list[list[float] | None]:
        # / embed many strings server-side via /api/embed; one http call per batch_size chunk
        if not texts:
            return []
        results: list[list[float] | None] = [None] * len(texts)
        for start in range(0, len(texts), batch_size):
            chunk = texts[start:start + batch_size]
            # / filter out empty/whitespace strings per /api/embed contract
            valid_pairs = [(i, t) for i, t in enumerate(chunk) if t and t.strip()]
            if not valid_pairs:
                continue
            indices = [i for i, _ in valid_pairs]
            payload_texts = [t for _, t in valid_pairs]
            try:
                client = await self._get_client()
                resp = await client.post(
                    f"{self._base_url}/api/embed",
                    json={"model": self._model, "input": payload_texts},
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                vecs = data.get("embeddings") or []
                if len(vecs) != len(payload_texts):
                    logger.warning(
                        "ollama_embed_batch_shape_mismatch",
                        got=len(vecs), expected=len(payload_texts),
                    )
                    continue
                for local_i, vec in zip(indices, vecs):
                    if isinstance(vec, list) and len(vec) == EMBED_DIM:
                        results[start + local_i] = [float(v) for v in vec]
            except Exception as exc:
                logger.info("ollama_embed_batch_failed", error=str(exc)[:120])
        return results
