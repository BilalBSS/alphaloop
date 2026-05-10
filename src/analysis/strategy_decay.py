
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


def _score_decay(pnl_arr: np.ndarray, strategy_id: str) -> DecaySignal | None:

    # / rolling sharpe (annualized)
    window = pnl_arr[-ROLLING_WINDOW:] if len(pnl_arr) >= ROLLING_WINDOW else pnl_arr
    mean_pnl = window.mean()
    std_pnl = window.std()
    rolling_sharpe = float(mean_pnl / std_pnl * np.sqrt(252)) if std_pnl > 0 else 0.0

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


async def check_strategy_decay(pool, strategy_id: str) -> DecaySignal | None:
    # / single-strategy decay check
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT pnl FROM trade_log
            WHERE strategy_id = $1 AND pnl IS NOT NULL
            ORDER BY created_at DESC LIMIT 100""",
            strategy_id,
        )
    if len(rows) < MIN_TRADES:
        return None
    pnl_arr = np.array([float(r["pnl"]) for r in reversed(rows)])
    return _score_decay(pnl_arr, strategy_id)


async def check_all_decay(pool) -> list[DecaySignal]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT strategy_id, pnl FROM (
                SELECT strategy_id, pnl, created_at,
                       ROW_NUMBER() OVER (PARTITION BY strategy_id ORDER BY created_at DESC) AS rn
                FROM trade_log
                WHERE strategy_id IS NOT NULL AND pnl IS NOT NULL
            ) t WHERE rn <= 100
            ORDER BY strategy_id, rn DESC"""
        )
    by_strategy: dict[str, list[float]] = {}
    for r in rows:
        by_strategy.setdefault(r["strategy_id"], []).append(float(r["pnl"]))
    signals: list[DecaySignal] = []
    for sid, pnls in by_strategy.items():
        if len(pnls) < MIN_TRADES:
            continue
        sig = _score_decay(np.array(pnls), sid)
        if sig:
            signals.append(sig)
    return signals


async def check_all_strategies(pool, strategy_pool) -> list[DecaySignal]:
    signals = []
    for entry in strategy_pool.list_by_status("promoted"):
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
