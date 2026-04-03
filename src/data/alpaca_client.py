# / shared alpaca http client with connection pooling
# / lazy-initialized, reused across broker + tools + market_data + dashboard

from __future__ import annotations

import os

import httpx
import structlog

logger = structlog.get_logger(__name__)

PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"

# / module-level client
_client: httpx.AsyncClient | None = None


def alpaca_headers() -> dict[str, str]:
    # / auth headers -- reads env at call time so tests can mock
    return {
        "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
    }


def alpaca_base_url() -> str:
    return os.environ.get("ALPACA_BASE_URL", PAPER_URL)


async def get_alpaca_client() -> httpx.AsyncClient:
    # / lazy-init shared client
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=15.0)
    return _client


async def close_alpaca_client() -> None:
    # / call on shutdown
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None
