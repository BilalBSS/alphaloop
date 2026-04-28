# / phase 5 success-criteria metrics
# / phase 5 step 9: measure whether the flywheel actually spins
# /
# / all phase 5 success criteria from plans/phase5-activation.md are measurable
# / via SQL over our own tables. this module computes them in one shot so the
# / dashboard Health tab can render a "flywheel health" panel.
# /
# / success criteria (from plan):
# /   - at least 1 strategy killed+replaced in 30d via evolution
# /   - brier populated for >= 3 strategies
# /   - cerebras call count > 0 weekly
# /   - wiki "established" docs > 15
# /   - regime diversity > 2 per asset class

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class Phase5Metrics:
    """snapshot of phase 5 activation health"""
    # / evolution
    days_since_last_evolution_kill: int | None
    evolution_kills_30d: int
    # / brier
    strategies_with_brier: int
    total_strategies: int
    # / llm split
    cerebras_calls_7d: int
    groq_calls_7d: int
    cerebras_call_pct_7d: float
    # / wiki
    established_wiki_count: int
    total_wiki_count: int
    # / regime
    equity_regime_diversity_7d: int
    crypto_regime_diversity_7d: int
    sector_regime_diversity_7d: int
    # / meta
    computed_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def success_criteria(self) -> dict[str, bool]:
        """evaluates each phase 5 success criterion as pass/fail"""
        return {
            "evolution_kill_last_30d": (self.evolution_kills_30d >= 1),
            "brier_populated_3plus": (self.strategies_with_brier >= 3),
            "cerebras_nonzero_7d": (self.cerebras_calls_7d > 0),
            "established_wiki_15plus": (self.established_wiki_count >= 15),
            "regime_diversity_2plus_equity": (self.equity_regime_diversity_7d >= 2),
            "regime_diversity_2plus_crypto": (self.crypto_regime_diversity_7d >= 2),
        }

    def all_pass(self) -> bool:
        return all(self.success_criteria().values())


async def _safe_fetch_one(pool, query: str, *args) -> Any:
    # / run a query that should return one row, return None if table missing
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return row
    except Exception as exc:
        logger.debug("phase5_metric_query_failed", query=query[:60], error=str(exc)[:100])
        return None


async def _safe_fetch_val(pool, query: str, *args) -> Any:
    row = await _safe_fetch_one(pool, query, *args)
    if row is None:
        return None
    return row[0] if len(row) > 0 else None


async def _evolution_stats(pool) -> tuple[int | None, int]:
    # / (days_since_last_kill, kills_in_last_30d)
    # / evolution_log has killed_count column — read from there
    days = None
    last_kill_row = await _safe_fetch_one(
        pool,
        """SELECT created_at FROM evolution_log
        WHERE killed_count > 0
        ORDER BY created_at DESC LIMIT 1""",
    )
    if last_kill_row is not None and last_kill_row[0] is not None:
        last = last_kill_row[0]
        if isinstance(last, datetime):
            now = datetime.now(timezone.utc)
            # / normalize for naive datetime in db
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            days = (now - last).days

    kills_30d = await _safe_fetch_val(
        pool,
        """SELECT COALESCE(SUM(killed_count), 0) FROM evolution_log
        WHERE created_at >= NOW() - INTERVAL '30 days'""",
    )
    return days, int(kills_30d or 0)


async def _brier_stats(pool) -> tuple[int, int]:
    # / (strategies_with_brier, total_strategies)
    with_brier = await _safe_fetch_val(
        pool,
        "SELECT COUNT(*) FROM strategy_scores WHERE brier_score IS NOT NULL",
    )
    total = await _safe_fetch_val(
        pool,
        "SELECT COUNT(DISTINCT strategy_id) FROM strategy_scores",
    )
    return int(with_brier or 0), int(total or 0)


async def _llm_split_stats(pool) -> tuple[int, int, float]:
    # / (cerebras_calls_7d, groq_calls_7d, cerebras_pct)
    cerebras = await _safe_fetch_val(
        pool,
        """SELECT COALESCE(SUM(request_count), 0) FROM api_costs
        WHERE source = 'cerebras' AND date >= CURRENT_DATE - INTERVAL '7 days'""",
    )
    groq = await _safe_fetch_val(
        pool,
        """SELECT COALESCE(SUM(request_count), 0) FROM api_costs
        WHERE source = 'groq' AND date >= CURRENT_DATE - INTERVAL '7 days'""",
    )
    cerebras = int(cerebras or 0)
    groq = int(groq or 0)
    total = cerebras + groq
    pct = (cerebras / total) if total > 0 else 0.0
    return cerebras, groq, pct


async def _wiki_stats(pool) -> tuple[int, int]:
    # / (established_count, total_count)
    established = await _safe_fetch_val(
        pool,
        "SELECT COUNT(*) FROM wiki_documents WHERE confidence = 'established'",
    )
    total = await _safe_fetch_val(
        pool, "SELECT COUNT(*) FROM wiki_documents",
    )
    return int(established or 0), int(total or 0)


async def _regime_diversity(pool, market: str) -> int:
    # / distinct regimes observed for `market` in last 7 days
    val = await _safe_fetch_val(
        pool,
        """SELECT COUNT(DISTINCT regime) FROM regime_history
        WHERE market = $1 AND created_at >= NOW() - INTERVAL '7 days'""",
        market,
    )
    return int(val or 0)


async def _sector_regime_diversity(pool) -> int:
    # / count DISTINCT (sector_market, regime) pairs in last 7 days for sector rows
    # / sector markets are strings like 'mega_tech', 'cloud_cyber' — not 'equity' or 'crypto'
    val = await _safe_fetch_val(
        pool,
        """SELECT COUNT(DISTINCT regime) FROM regime_history
        WHERE market NOT IN ('equity', 'crypto')
        AND created_at >= NOW() - INTERVAL '7 days'""",
    )
    return int(val or 0)


async def compute_phase5_metrics(pool) -> Phase5Metrics:
    """computes all phase 5 activation metrics in one round-trip.

    returns a Phase5Metrics dataclass. on query failure for a given field, the
    field is filled with a safe zero/None — the endpoint must not 500 just
    because one table doesn't exist yet.
    """
    days_since_kill, kills_30d = await _evolution_stats(pool)
    with_brier, total_strat = await _brier_stats(pool)
    cerebras_calls, groq_calls, cerebras_pct = await _llm_split_stats(pool)
    established, total_wiki = await _wiki_stats(pool)
    equity_div = await _regime_diversity(pool, "equity")
    crypto_div = await _regime_diversity(pool, "crypto")
    sector_div = await _sector_regime_diversity(pool)

    return Phase5Metrics(
        days_since_last_evolution_kill=days_since_kill,
        evolution_kills_30d=kills_30d,
        strategies_with_brier=with_brier,
        total_strategies=total_strat,
        cerebras_calls_7d=cerebras_calls,
        groq_calls_7d=groq_calls,
        cerebras_call_pct_7d=round(cerebras_pct, 4),
        established_wiki_count=established,
        total_wiki_count=total_wiki,
        equity_regime_diversity_7d=equity_div,
        crypto_regime_diversity_7d=crypto_div,
        sector_regime_diversity_7d=sector_div,
        computed_at=datetime.now(timezone.utc).isoformat(),
    )
