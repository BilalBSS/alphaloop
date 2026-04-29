
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

_COST_RATES: dict[str, tuple[float, float]] = {
    "groq": (0.0, 0.0),
    "cerebras": (0.0, 0.0),
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    "ollama-nomic-embed-text": (0.0, 0.0),
}

_daily_costs: dict[tuple[date, str], dict] = defaultdict(lambda: {
    "call_count": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0,
})


def track_llm_cost(provider: str, model: str, tokens_in: int, tokens_out: int) -> None:
    key = (date.today(), provider)
    entry = _daily_costs[key]
    entry["call_count"] += 1
    entry["tokens_in"] += tokens_in
    entry["tokens_out"] += tokens_out
    rates = _COST_RATES.get(model, _COST_RATES.get(provider, (0.0, 0.0)))
    cost = (tokens_in * rates[0] + tokens_out * rates[1]) / 1_000_000
    entry["cost"] += cost


def track_api_call(source: str) -> None:
    key = (date.today(), source)
    _daily_costs[key]["call_count"] += 1


def get_daily_summary() -> dict:
    today = date.today()
    return {
        source: dict(stats)
        for (d, source), stats in _daily_costs.items()
        if d == today
    }


async def flush_to_db(pool: asyncpg.Pool) -> int:
    if not _daily_costs:
        return 0
    written = 0
    async with pool.acquire() as conn:
        for (d, source), stats in list(_daily_costs.items()):
            try:
                await conn.execute(
                    """INSERT INTO api_costs (date, source, call_count, tokens_in, tokens_out, estimated_cost_usd)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (date, source) DO UPDATE SET
                        call_count = api_costs.call_count + EXCLUDED.call_count,
                        tokens_in = api_costs.tokens_in + EXCLUDED.tokens_in,
                        tokens_out = api_costs.tokens_out + EXCLUDED.tokens_out,
                        estimated_cost_usd = api_costs.estimated_cost_usd + EXCLUDED.estimated_cost_usd""",
                    d, source, stats["call_count"], stats["tokens_in"],
                    stats["tokens_out"], Decimal(str(round(stats["cost"], 6))),
                )
                written += 1
            except Exception as exc:
                logger.warning("cost_tracker_flush_failed", source=source, error=str(exc))
    _daily_costs.clear()
    return written


