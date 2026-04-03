# / shared llm http clients for groq and deepseek
# / lazy-initialized, long-lived clients with connection pooling

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_PROVIDER_CONFIG = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "timeout": 15.0,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
        "timeout": 30.0,
    },
}

# / module-level clients, one per provider
_clients: dict[str, httpx.AsyncClient] = {}


async def get_llm_client(provider: str) -> httpx.AsyncClient:
    # / lazy-init shared client per provider
    if provider not in _PROVIDER_CONFIG:
        raise ValueError(f"unknown llm provider: {provider}")

    client = _clients.get(provider)
    if client is None or client.is_closed:
        cfg = _PROVIDER_CONFIG[provider]
        _clients[provider] = httpx.AsyncClient(timeout=cfg["timeout"])

    return _clients[provider]


async def close_llm_clients() -> None:
    # / call on shutdown to cleanly close all llm clients
    for name, client in list(_clients.items()):
        if client is not None and not client.is_closed:
            await client.aclose()
    _clients.clear()


async def llm_call(
    provider: str,
    messages: list[dict[str, str]],
    model: str | None = None,
    timeout: float | None = None,
    **kwargs: Any,
) -> dict:
    # / unified llm api call -- supports "groq" and "deepseek"
    # / returns parsed json response or raises
    cfg = _PROVIDER_CONFIG.get(provider)
    if cfg is None:
        raise ValueError(f"unknown llm provider: {provider}")

    api_key = os.environ.get(cfg["env_key"], "")
    if not api_key:
        raise ValueError(f"missing {cfg['env_key']} env var")

    client = await get_llm_client(provider)

    payload: dict[str, Any] = {"messages": messages, **kwargs}
    if model:
        payload["model"] = model

    resp = await client.post(
        f"{cfg['base_url']}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout or cfg["timeout"],
    )
    resp.raise_for_status()
    return resp.json()
