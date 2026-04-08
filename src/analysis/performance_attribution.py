# / decompose strategy returns into factor contributions

from __future__ import annotations

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class Attribution:
    strategy_id: str
    total_return: float
    market_contribution: float
    sector_contribution: float
    stock_contribution: float
    alpha: float


async def compute_attribution(
    pool,
    strategy_id: str,
    benchmark_return: float = 0.0,
) -> Attribution | None:
    # / compute simple brinson-style attribution from trade_log
    async with pool.acquire() as conn:
        trades = await conn.fetch(
            """SELECT symbol, pnl, side, created_at FROM trade_log
            WHERE strategy_id = $1 AND pnl IS NOT NULL
            ORDER BY created_at""",
            strategy_id,
        )

    if not trades:
        return None

    total_pnl = sum(float(t["pnl"]) for t in trades)

    # / group pnl by sector
    from src.data.symbols import get_sector
    sector_pnl: dict[str, float] = {}
    for t in trades:
        sector = get_sector(t["symbol"]) or "unknown"
        sector_pnl[sector] = sector_pnl.get(sector, 0) + float(t["pnl"])

    # / simplified attribution
    market_contrib = benchmark_return * len(trades) * 0.01
    sector_contrib = sum(abs(v) for v in sector_pnl.values()) - abs(total_pnl)
    alpha = total_pnl - market_contrib

    return Attribution(
        strategy_id=strategy_id,
        total_return=total_pnl,
        market_contribution=market_contrib,
        sector_contribution=max(0, sector_contrib),
        stock_contribution=0.0,
        alpha=alpha,
    )


async def store_attribution(pool, attr: Attribution) -> None:
    # / persist performance attribution to db
    from datetime import date
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO performance_attribution
                (strategy_id, period_start, period_end, total_return,
                 market_contribution, sector_contribution, stock_contribution, alpha)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT DO NOTHING""",
                attr.strategy_id, date.today(), date.today(), attr.total_return,
                attr.market_contribution, attr.sector_contribution,
                attr.stock_contribution, attr.alpha,
            )
    except Exception as exc:
        logger.warning("store_attribution_failed", strategy_id=attr.strategy_id, error=str(exc))
