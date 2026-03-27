# / on-chain data via dune analytics
# / execute query -> poll results pattern, 3 concurrent max

from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

from .resilience import api_get, api_post, configure_rate_limit, with_retry

logger = structlog.get_logger(__name__)

DUNE_BASE = "https://api.dune.com/api/v1"

configure_rate_limit("dune", max_concurrent=3, delay=1.0)


def _dune_headers() -> dict[str, str]:
    key = os.environ.get("DUNE_API_KEY", "")
    if not key:
        logger.debug("dune_api_key_missing")
    return {"x-dune-api-key": key}


@with_retry(source="dune", max_retries=2, base_delay=5.0)
async def execute_query(query_id: int, params: dict | None = None) -> str | None:
    # / submit a dune query for execution, returns execution_id
    if not os.environ.get("DUNE_API_KEY"):
        return None

    url = f"{DUNE_BASE}/query/{query_id}/execute"
    body: dict[str, Any] = {}
    if params:
        body["query_parameters"] = params

    resp = await api_post(url, headers=_dune_headers(), json=body, source="dune")
    data = resp.json()
    return data.get("execution_id")


async def poll_results(
    execution_id: str, max_polls: int = 30, poll_interval: float = 2.0,
) -> list[dict[str, Any]]:
    # / poll until query completes or timeout
    if not os.environ.get("DUNE_API_KEY"):
        return []

    url = f"{DUNE_BASE}/execution/{execution_id}/results"
    for _ in range(max_polls):
        try:
            resp = await api_get(url, headers=_dune_headers(), source="dune")
            data = resp.json()
            state = data.get("state")

            if state == "QUERY_STATE_COMPLETED":
                return data.get("result", {}).get("rows", [])
            elif state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
                logger.warning("dune_query_failed", execution_id=execution_id, state=state)
                return []
        except Exception as exc:
            logger.warning("dune_poll_error", error=str(exc))

        await asyncio.sleep(poll_interval)

    logger.warning("dune_query_timeout", execution_id=execution_id)
    return []


async def run_query(query_id: int, params: dict | None = None) -> list[dict[str, Any]]:
    # / convenience: execute + poll in one call
    execution_id = await execute_query(query_id, params)
    if not execution_id:
        return []
    return await poll_results(execution_id)


async def fetch_active_addresses(chain: str = "ethereum") -> list[dict[str, Any]]:
    # / active addresses trend from configured dune query
    query_id = int(os.environ.get("DUNE_QUERY_ACTIVE_ADDRESSES", "0"))
    if not query_id:
        return []
    return await run_query(query_id, {"chain": chain})


async def fetch_exchange_flows(symbol: str = "ETH") -> list[dict[str, Any]]:
    # / exchange inflow/outflow from configured dune query
    query_id = int(os.environ.get("DUNE_QUERY_EXCHANGE_FLOWS", "0"))
    if not query_id:
        return []
    return await run_query(query_id, {"token": symbol})


async def fetch_whale_transactions(min_usd: int = 1_000_000) -> list[dict[str, Any]]:
    # / large transactions from configured dune query
    query_id = int(os.environ.get("DUNE_QUERY_WHALE_TRANSACTIONS", "0"))
    if not query_id:
        return []
    return await run_query(query_id, {"min_usd": str(min_usd)})
