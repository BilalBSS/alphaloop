# / detect strategy performance degradation via rolling sharpe + cusum

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

ROLLING_WINDOW = 30
MIN_TRADES = 10
SHARPE_THRESHOLD = 0.5
CUSUM_SIGMA = 2.0


@dataclass
class DecaySignal:
    strategy_id: str
    rolling_sharpe: float
    days_below_threshold: int
    cusum_triggered: bool
    recommendation: str


async def check_strategy_decay(pool, strategy_id: str) -> DecaySignal | None:
    # / check if a strategy is decaying based on recent trade performance
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT pnl, created_at FROM trade_log
            WHERE strategy_id = $1 AND pnl IS NOT NULL
            ORDER BY created_at DESC LIMIT 100""",
            strategy_id,
        )

    if len(rows) < MIN_TRADES:
        return None

    pnls = [float(r["pnl"]) for r in reversed(rows)]
    pnl_arr = np.array(pnls)

    # / rolling sharpe (annualized)
    window = pnl_arr[-ROLLING_WINDOW:] if len(pnl_arr) >= ROLLING_WINDOW else pnl_arr
    mean_pnl = window.mean()
    std_pnl = window.std()
    rolling_sharpe = float(mean_pnl / std_pnl * np.sqrt(252)) if std_pnl > 0 else 0.0

    # / count consecutive periods below threshold
    days_below = 0
    for i in range(len(pnl_arr) - 1, max(len(pnl_arr) - ROLLING_WINDOW - 1, -1), -1):
        chunk = pnl_arr[max(0, i - ROLLING_WINDOW + 1):i + 1]
        if len(chunk) < 5:
            break
        s = chunk.mean() / chunk.std() * np.sqrt(252) if chunk.std() > 0 else 0.0
        if s < SHARPE_THRESHOLD:
            days_below += 1
        else:
            break

    # / cusum changepoint detection (downward shift)
    target_mean = pnl_arr.mean()
    target_std = pnl_arr.std() if pnl_arr.std() > 0 else 1.0
    cusum_neg = 0.0
    cusum_triggered = False
    threshold = CUSUM_SIGMA * target_std

    for p in pnl_arr:
        cusum_neg = min(0, cusum_neg + (p - target_mean + threshold / 2))
        if abs(cusum_neg) > threshold:
            cusum_triggered = True
            break

    # / recommendation
    if rolling_sharpe < 0 and days_below > 14:
        rec = "kill"
    elif rolling_sharpe < SHARPE_THRESHOLD and (days_below > 7 or cusum_triggered):
        rec = "demote"
    elif rolling_sharpe < SHARPE_THRESHOLD:
        rec = "monitor"
    else:
        rec = "ok"

    return DecaySignal(
        strategy_id=strategy_id,
        rolling_sharpe=rolling_sharpe,
        days_below_threshold=days_below,
        cusum_triggered=cusum_triggered,
        recommendation=rec,
    )


async def check_all_strategies(pool, strategy_pool) -> list[DecaySignal]:
    # / check decay for all live strategies
    signals = []
    for entry in strategy_pool.list_by_status("live"):
        signal = await check_strategy_decay(pool, entry.strategy.strategy_id)
        if signal and signal.recommendation != "ok":
            signals.append(signal)
            logger.warning(
                "strategy_decay_detected",
                strategy_id=signal.strategy_id,
                sharpe=signal.rolling_sharpe,
                recommendation=signal.recommendation,
            )
    return signals
