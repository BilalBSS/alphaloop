# / bug a: live strategy metrics writer
# / aggregates closed trades from trade_log, computes rolling sharpe/sortino/maxdd/win rate
# / upserts to strategy_scores so /api/quant-metrics returns real rows for live strategies
# / evolution engine backtester only writes backtest results; this covers the live path

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import numpy as np
import structlog

from src.agents import tools
from src.quant.risk_metrics import max_drawdown

logger = structlog.get_logger(__name__)

# / minimum closed trades per strategy/window before metrics are emitted
MIN_TRADES = 3
# / annualization factor (us equity trading days)
# / note: sharpe/sortino are computed on per-trade returns and annualized by sqrt(252)
# / matches src/strategies/backtest.py:439 so live metrics match backtest metrics
ANNUAL_TRADING_DAYS = 252
# / default paper trading account base; used as denominator for max drawdown %
DEFAULT_BASE_CAPITAL = 100_000.0
# / how many days of stale live-metric rows to retain before cleanup
LIVE_METRIC_RETENTION_DAYS = 3


async def compute_live_strategy_metrics(
    pool, windows_days: list[int] | None = None,
    base_capital: float = DEFAULT_BASE_CAPITAL,
) -> int:
    # / compute rolling metrics per strategy × window, upsert into strategy_scores
    # / returns number of (strategy_id, window) rows upserted
    windows_days = windows_days or [30, 90]
    today = date.today()
    upserted = 0

    # / cleanup: bound strategy_scores growth by deleting stale live-metric rows
    # / identified via regime_breakdown->>'source' marker set on write
    async with pool.acquire() as conn:
        await conn.execute(
            f"""DELETE FROM strategy_scores
            WHERE regime_breakdown->>'source' = 'live_metrics'
              AND period_end < CURRENT_DATE - INTERVAL '{LIVE_METRIC_RETENTION_DAYS} days'"""
        )
        strats = await conn.fetch(
            """SELECT DISTINCT strategy_id FROM trade_log
            WHERE strategy_id IS NOT NULL AND strategy_id != 'untracked'
            ORDER BY strategy_id"""
        )
    strategy_ids = [r["strategy_id"] for r in strats]

    for strategy_id in strategy_ids:
        for window_days in windows_days:
            period_start = today - timedelta(days=window_days)
            try:
                result = await _compute_for_strategy(
                    pool, strategy_id, period_start, today, base_capital,
                )
                if result is None:
                    continue
                await tools.store_strategy_score(
                    pool,
                    strategy_id=strategy_id,
                    period_start=period_start,
                    period_end=today,
                    sharpe_ratio=result["sharpe_ratio"],
                    max_drawdown=result["max_drawdown_pct"],
                    win_rate=result["win_rate"],
                    brier_score=result.get("brier_score"),
                    total_trades=result["total_trades"],
                    sortino_ratio=result.get("sortino_ratio"),
                    composite_score=result.get("composite_score"),
                    regime_breakdown={
                        "source": "live_metrics",
                        "window_days": window_days,
                        "total_pnl": result.get("total_pnl", 0.0),
                    },
                )
                upserted += 1
            except Exception as exc:
                logger.warning(
                    "live_metrics_error",
                    strategy_id=strategy_id,
                    window_days=window_days,
                    error=str(exc),
                )

    if upserted:
        logger.info("live_strategy_metrics_written", upserted=upserted, windows=windows_days)
    return upserted


async def _compute_for_strategy(
    pool, strategy_id: str, period_start: date, period_end: date,
    base_capital: float = DEFAULT_BASE_CAPITAL,
) -> dict[str, Any] | None:
    # / fetch trade_log rows in window, fifo-match buys/sells, compute metrics
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT symbol, side, qty, price, pnl, created_at
            FROM trade_log
            WHERE strategy_id = $1
              AND created_at >= $2
              AND created_at <= $3
            ORDER BY created_at ASC""",
            strategy_id, period_start, period_end,
        )

    if not rows:
        return None

    returns, total_pnl, closed_trades = _fifo_match_returns([dict(r) for r in rows])

    if len(closed_trades) < MIN_TRADES:
        return None

    returns_arr = np.array(returns, dtype=np.float64)

    # / sharpe (annualized; treats trade returns as iid, consistent with backtest.py:439)
    avg_return = float(np.mean(returns_arr))
    std_return = float(np.std(returns_arr, ddof=1)) if len(returns_arr) > 1 else 0.0
    sharpe = (avg_return / std_return * math.sqrt(ANNUAL_TRADING_DAYS)) if std_return > 0 else 0.0

    # / sortino (downside deviation only)
    downside = returns_arr[returns_arr < 0]
    downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0
    sortino = (
        avg_return / downside_std * math.sqrt(ANNUAL_TRADING_DAYS)
    ) if downside_std > 0 else 0.0

    # / win rate
    wins = int(np.sum(returns_arr > 0))
    win_rate = wins / len(returns_arr)

    # / max drawdown against a fixed capital base for meaningful % scaling
    # / uses base_capital (default $100k paper trading account) so drawdown
    # / is a real percentage of account equity, not a synthetic unit ratio
    pnl_series = np.array([t["pnl"] for t in closed_trades], dtype=np.float64)
    equity_curve = base_capital + np.cumsum(pnl_series)
    try:
        _, max_dd_pct = max_drawdown(equity_curve)
    except Exception:
        max_dd_pct = 0.0

    # / composite score matches evolution ranking formula (sharpe 0.4 / win 0.3 / dd 0.3)
    composite = sharpe * 0.4 + win_rate * 0.3 - max_dd_pct * 0.3

    return {
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "win_rate": round(win_rate, 4),
        "total_trades": len(closed_trades),
        "composite_score": round(composite, 4),
        "total_pnl": round(total_pnl, 2),
        "brier_score": None,  # / needs prediction+outcome pairs, wired in later phase
    }


def _fifo_match_returns(
    rows: list[dict],
) -> tuple[list[float], float, list[dict]]:
    # / fifo match: sells consume oldest open buy lots per symbol
    # / returns (per-trade-return pct list, total realized pnl, closed trade records)
    open_lots: dict[str, list[dict]] = {}
    returns: list[float] = []
    total_pnl = 0.0
    closed_trades: list[dict] = []

    for r in rows:
        symbol = r["symbol"]
        side = r["side"]
        qty = float(r["qty"]) if r["qty"] is not None else 0.0
        price = float(r["price"]) if r["price"] is not None else 0.0

        if qty <= 0 or price <= 0:
            continue

        if side == "buy":
            open_lots.setdefault(symbol, []).append({
                "qty": qty, "price": price, "created_at": r.get("created_at"),
            })
        elif side == "sell":
            remaining = qty
            lots = open_lots.get(symbol, [])
            while remaining > 0 and lots:
                lot = lots[0]
                take = min(remaining, lot["qty"])
                pnl = (price - lot["price"]) * take
                ret = (price - lot["price"]) / lot["price"] if lot["price"] > 0 else 0.0
                returns.append(ret)
                total_pnl += pnl
                closed_trades.append({
                    "symbol": symbol,
                    "pnl": pnl,
                    "return": ret,
                    "entry_price": lot["price"],
                    "exit_price": price,
                    "qty": take,
                })
                lot["qty"] -= take
                remaining -= take
                if lot["qty"] <= 0:
                    lots.pop(0)

    return returns, total_pnl, closed_trades
