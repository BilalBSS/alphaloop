# / shared llm http clients for groq and deepseek
# / lazy-initialized, long-lived clients with connection pooling

from __future__ import annotations

import os
import random
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
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "env_key": "CEREBRAS_API_KEY",
        "timeout": 15.0,
    },
}

# / module-level clients, one per provider
_clients: dict[str, httpx.AsyncClient] = {}

# / providers that can be primary in the 4-tier fallback chain
_FALLBACK_PROVIDERS = ("groq", "cerebras")

# / default split: 90% groq, 10% cerebras to keep the fallback path warm
_DEFAULT_PROVIDER_SPLIT = "groq:0.9,cerebras:0.1"


def _parse_provider_split(raw: str | None) -> list[tuple[str, float]]:
    # / parse "groq:0.9,cerebras:0.1" into normalized (provider, weight) pairs
    # / empty/invalid input falls back to pure groq (no routing change)
    if not raw or not raw.strip():
        return [("groq", 1.0)]

    pairs: list[tuple[str, float]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            logger.warning("llm_provider_split_bad_entry", entry=chunk)
            continue
        name, _, weight_str = chunk.partition(":")
        name = name.strip().lower()
        if name not in _FALLBACK_PROVIDERS:
            logger.warning("llm_provider_split_unknown_provider", provider=name)
            continue
        try:
            weight = float(weight_str.strip())
        except ValueError:
            logger.warning("llm_provider_split_bad_weight", entry=chunk)
            continue
        if weight < 0:
            logger.warning("llm_provider_split_negative_weight", provider=name, weight=weight)
            continue
        pairs.append((name, weight))

    if not pairs:
        logger.warning("llm_provider_split_no_valid_entries_using_default", raw=raw)
        return [("groq", 1.0)]

    total = sum(w for _, w in pairs)
    if total <= 0:
        logger.warning("llm_provider_split_zero_total_using_default", raw=raw)
        return [("groq", 1.0)]

    # / normalize so weights sum to 1.0
    return [(name, w / total) for name, w in pairs]


# / parse once at module load — re-reading env on every call is wasteful
_PROVIDER_SPLIT: list[tuple[str, float]] = _parse_provider_split(
    os.environ.get("LLM_PROVIDER_SPLIT", _DEFAULT_PROVIDER_SPLIT),
)


def reload_provider_split() -> list[tuple[str, float]]:
    # / re-read LLM_PROVIDER_SPLIT from env; useful for tests and runtime config reload
    global _PROVIDER_SPLIT
    _PROVIDER_SPLIT = _parse_provider_split(
        os.environ.get("LLM_PROVIDER_SPLIT", _DEFAULT_PROVIDER_SPLIT),
    )
    return _PROVIDER_SPLIT


def _pick_primary_provider() -> str:
    # / weighted random pick of the 4-tier chain's primary provider
    names = [n for n, _ in _PROVIDER_SPLIT]
    weights = [w for _, w in _PROVIDER_SPLIT]
    if len(names) == 1:
        return names[0]
    pick = random.choices(names, weights=weights, k=1)[0]
    # / log with the raw roll so we can verify distribution in production
    roll = random.random()
    logger.info("llm_primary_picked", provider=pick, roll=round(roll, 4))
    return pick


def build_fallback_chain(
    groq_fast: str,
    cerebras_fast: str,
    groq_slow: str,
    cerebras_slow: str,
) -> list[tuple[str, str]]:
    # / return the ordered (provider, model) attempts list with primary chosen by weighted roll
    # / groq-primary: groq 70b -> cerebras 70b -> groq 120b -> cerebras 120b
    # / cerebras-primary: cerebras 70b -> groq 70b -> cerebras 120b -> groq 120b
    primary = _pick_primary_provider()
    if primary == "cerebras":
        return [
            ("cerebras", cerebras_fast),
            ("groq", groq_fast),
            ("cerebras", cerebras_slow),
            ("groq", groq_slow),
        ]
    return [
        ("groq", groq_fast),
        ("cerebras", cerebras_fast),
        ("groq", groq_slow),
        ("cerebras", cerebras_slow),
    ]


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
    data = resp.json()

    # / track cost from usage field
    try:
        from .cost_tracker import track_llm_cost
        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)
        if not tokens_in:
            tokens_in = sum(len(m.get("content", "")) for m in messages) // 4
        if not tokens_out:
            choices = data.get("choices", [])
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            tokens_out = len(content) // 4
        track_llm_cost(provider, model or "", tokens_in, tokens_out)
    except Exception:
        pass

    return data
