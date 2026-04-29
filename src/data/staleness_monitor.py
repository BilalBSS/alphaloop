# / monitor freshness of all data sources, alert on stale data

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class SourceFreshness:
    source: str
    last_update: datetime | None
    staleness_hours: float
    threshold_hours: float
    is_stale: bool


# / staleness thresholds per source (hours)
# / market_data (daily bars) → 28h = one trading day + buffer. weekends can legitimately exceed 24h.
# / market_data_crypto → checked against intraday 1h bars not daily; 6h gives buffer for backfill cadence
# / regime_history → 36h = updated every 6h by _regime_loop, buffer for weekend misses
FRESHNESS_THRESHOLDS = {
    "market_data": 28,
    "market_data_crypto": 6,
    "fundamentals": 168,
    "insider_trades": 336,
    "news_sentiment": 48,
    "social_sentiment": 48,
    "regime_history": 36,
    "analysis_scores": 4,
    "computed_indicators": 2,
}


async def check_all_freshness(pool: asyncpg.Pool) -> list[SourceFreshness]:
    # / check freshness of all data sources
    results = []
    now = datetime.now(timezone.utc)

    queries = {
        "market_data": "SELECT MAX(created_at) FROM market_data WHERE symbol NOT LIKE '%%USD'",
        # / check crypto against intraday 1h bars (24/7 feed) not daily
        "market_data_crypto": "SELECT MAX(created_at) FROM market_data_intraday WHERE symbol LIKE '%%USD'",
        "fundamentals": "SELECT MAX(created_at) FROM fundamentals",
        "insider_trades": "SELECT MAX(created_at) FROM insider_trades",
        "news_sentiment": "SELECT MAX(created_at) FROM news_sentiment",
        "social_sentiment": "SELECT MAX(created_at) FROM social_sentiment",
        "regime_history": "SELECT MAX(created_at) FROM regime_history",
        "analysis_scores": "SELECT MAX(created_at) FROM analysis_scores",
        "computed_indicators": "SELECT MAX(created_at) FROM computed_indicators",
    }

    async with pool.acquire() as conn:
        for source, query in queries.items():
            threshold = FRESHNESS_THRESHOLDS.get(source, 24)
            try:
                row = await conn.fetchrow(query)
                last_update = row[0] if row and row[0] else None

                if last_update is None:
                    staleness = float("inf")
                    is_stale = True
                else:
                    if last_update.tzinfo is None:
                        last_update = last_update.replace(tzinfo=timezone.utc)
                    staleness = (now - last_update).total_seconds() / 3600
                    is_stale = staleness > threshold

                results.append(SourceFreshness(
                    source=source,
                    last_update=last_update,
                    staleness_hours=round(staleness, 1),
                    threshold_hours=threshold,
                    is_stale=is_stale,
                ))

                if is_stale:
                    logger.warning("data_stale", source=source, hours=round(staleness, 1), threshold=threshold)
            except Exception as exc:
                logger.warning("freshness_check_failed", source=source, error=str(exc))
                results.append(SourceFreshness(
                    source=source, last_update=None,
                    staleness_hours=float("inf"), threshold_hours=threshold, is_stale=True,
                ))

    return results
