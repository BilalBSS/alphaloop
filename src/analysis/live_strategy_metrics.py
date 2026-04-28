# / live strategy metrics writer
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
from src.quant.brier_score import brier_score
from src.quant.risk_metrics import max_drawdown

logger = structlog.get_logger(__name__)

# / emit metrics once strategy has >=3 data points
# / counts closed trades + daily mtm of open positions
MIN_TRADES = 3
# / matches backtest sharpe annualization
ANNUAL_TRADING_DAYS = 252
# / default paper trading account base; used as denominator for max drawdown %
DEFAULT_BASE_CAPITAL = 100_000.0
# / how many days of stale live-metric rows to retain before cleanup
LIVE_METRIC_RETENTION_DAYS = 3


async def compute_live_strategy_metrics(
    pool, windows_days: list[int] | None = None,
    base_capital: float = DEFAULT_BASE_CAPITAL,
) -> int:
    # / compute rolling metrics per strategy/window, upsert into strategy_scores
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


async def _compute_open_position_returns(
    pool, strategy_id: str, period_start: date, period_end: date,
) -> tuple[list[float], float]:
    # / build daily mark-to-market return series from strategy's currently open positions
    # / for each open position, walk daily market_data closes and compute pct changes vs previous
    # / close (or entry price on the first day). returns per-position daily returns concatenated.
    # / total_pnl is the sum of unrealized p&l (current close vs entry) across all positions.
    async with pool.acquire() as conn:
        positions = await conn.fetch(
            """SELECT symbol, qty, avg_entry_price, opened_at
            FROM strategy_positions
            WHERE strategy_id = $1 AND qty > 0 AND avg_entry_price > 0""",
            strategy_id,
        )
        if not positions:
            return [], 0.0

        earliest_start = period_start
        symbols: list[str] = []
        for p in positions:
            entry_price = float(p["avg_entry_price"])
            qty = float(p["qty"])
            if entry_price <= 0 or qty <= 0:
                continue
            opened = p["opened_at"].date() if p["opened_at"] else period_start
            start = max(opened, period_start)
            if start > period_end:
                continue
            if start < earliest_start:
                earliest_start = start
            symbols.append(p["symbol"])
        if not symbols:
            return [], 0.0

        bars_rows = await conn.fetch(
            """SELECT symbol, date, close FROM market_data
            WHERE symbol = ANY($1::text[]) AND date >= $2 AND date <= $3
            ORDER BY symbol ASC, date ASC""",
            symbols, earliest_start, period_end,
        )

    bars_by_symbol: dict[str, list] = {}
    for r in bars_rows:
        bars_by_symbol.setdefault(r["symbol"], []).append(r)

    returns: list[float] = []
    total_unrealized = 0.0
    for p in positions:
        sym = p["symbol"]
        entry_price = float(p["avg_entry_price"])
        qty = float(p["qty"])
        if entry_price <= 0 or qty <= 0:
            continue
        opened = p["opened_at"].date() if p["opened_at"] else period_start
        start = max(opened, period_start)
        if start > period_end:
            continue
        bars = [b for b in bars_by_symbol.get(sym, []) if b["date"] >= start]
        prev = entry_price
        last_close = entry_price
        for b in bars:
            close = float(b["close"]) if b["close"] is not None else None
            if close is None or close <= 0 or prev <= 0:
                continue
            returns.append((close - prev) / prev)
            prev = close
            last_close = close
        # / unrealized p&l = latest close vs entry price * qty
        total_unrealized += (last_close - entry_price) * qty
    return returns, total_unrealized


async def _compute_for_strategy(
    pool, strategy_id: str, period_start: date, period_end: date,
    base_capital: float = DEFAULT_BASE_CAPITAL,
) -> dict[str, Any] | None:
    # / fetch trade_log rows in window, fifo-match buys/sells, compute metrics
    # / created_at is timestamptz; period_end is a date that casts to midnight.
    # / upper bound must be < period_end + 1 day to include trades that happened today.
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT symbol, side, qty, price, pnl, created_at
            FROM trade_log
            WHERE strategy_id = $1
              AND created_at >= $2
              AND created_at < ($3::date + INTERVAL '1 day')
            ORDER BY created_at ASC""",
            strategy_id, period_start, period_end,
        )

    returns: list[float] = []
    total_pnl = 0.0
    closed_trades: list[dict] = []
    if rows:
        returns, total_pnl, closed_trades = _fifo_match_returns([dict(r) for r in rows])

    # / extend returns with daily mark-to-market from open positions so paper-trading
    # / strategies get meaningful sharpe/sortino before any sell ever happens
    open_returns, unrealized_pnl = await _compute_open_position_returns(
        pool, strategy_id, period_start, period_end,
    )
    returns = returns + open_returns
    total_pnl += unrealized_pnl

    # / combined observation count gate — closed trades OR daily MTM observations
    if len(returns) < MIN_TRADES:
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
    # / also include unrealized p&l deltas from open positions so paper drawdown
    # / reflects ongoing mark-to-market moves, not just closed trade realized swings
    pnl_series_parts: list[float] = [t["pnl"] for t in closed_trades]
    if open_returns:
        # / convert mark-to-market returns to dollar p&l at base_capital scale for a comparable curve
        pnl_series_parts.extend(r * base_capital for r in open_returns)
    pnl_series = np.array(pnl_series_parts, dtype=np.float64) if pnl_series_parts else np.array([0.0])
    equity_curve = base_capital + np.cumsum(pnl_series)
    try:
        _, max_dd_pct = max_drawdown(equity_curve)
    except Exception:
        max_dd_pct = 0.0

    # / composite score matches evolution ranking formula (sharpe 0.4 / win 0.3 / dd 0.3)
    composite = sharpe * 0.4 + win_rate * 0.3 - max_dd_pct * 0.3

    # / brier calibration: how well did signal strength predict the outcome?
    brier = await _compute_brier(pool, strategy_id, period_start, period_end)

    return {
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "win_rate": round(win_rate, 4),
        # / total_trades = closed round-trips; observations covers MTM daily points too
        "total_trades": len(closed_trades),
        "total_observations": len(returns),
        "composite_score": round(composite, 4),
        "total_pnl": round(total_pnl, 2),
        "brier_score": round(brier, 4) if brier is not None else None,
    }


async def _compute_brier(
    pool, strategy_id: str, period_start: date, period_end: date,
) -> float | None:
    # / pair trade_signals.strength (predictions 0..1) with trade_log.pnl>0 (binary outcome).
    # / join on signal_id so each prediction is tied to its actual trade outcome.
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT ts.strength, tl.pnl
                FROM trade_signals ts
                JOIN approved_trades at_ ON at_.signal_id = ts.id
                JOIN trade_log tl ON tl.order_id = at_.order_id
                WHERE ts.strategy_id = $1
                  AND ts.signal_type = 'buy'
                  AND ts.strength IS NOT NULL
                  AND tl.pnl IS NOT NULL
                  AND tl.side = 'sell'
                  AND tl.created_at >= $2
                  AND tl.created_at < ($3::date + INTERVAL '1 day')""",
                strategy_id, period_start, period_end,
            )
        if not rows or len(rows) < 3:
            return None
        predictions = np.array([max(0.0, min(1.0, float(r["strength"]))) for r in rows])
        outcomes = np.array([1.0 if float(r["pnl"]) > 0 else 0.0 for r in rows])
        return float(brier_score(predictions, outcomes))
    except Exception as exc:
        logger.debug("brier_compute_failed", strategy_id=strategy_id, error=str(exc)[:100])
        return None


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
