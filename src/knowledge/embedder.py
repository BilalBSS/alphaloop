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
        # / embed a single string, returns None on any failure
        if not text or not text.strip():
            return None
        try:
            client = await self._get_client()
            resp = await client.post(
                f"{self._base_url}/api/embeddings",
                json={"model": self._model, "prompt": text},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            vec = data.get("embedding")
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
        # / embed many strings concurrently in bounded batches
        if not texts:
            return []
        results: list[list[float] | None] = [None] * len(texts)
        for start in range(0, len(texts), batch_size):
            chunk = texts[start:start + batch_size]
            tasks = [self.embed(t) for t in chunk]
            done = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(done):
                if isinstance(r, Exception):
                    logger.info("ollama_embed_batch_item_failed", error=str(r)[:120])
                    results[start + i] = None
                else:
                    results[start + i] = r
        return results
