# / shared db helpers for agent pipeline
# / all agents import from here instead of writing raw sql

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import structlog

from src.strategies.base_strategy import AnalysisData

logger = structlog.get_logger(__name__)

# / valid tables for status updates (whitelist prevents sql injection)
_STATUS_TABLES = {"trade_signals", "approved_trades"}


async def store_analysis_score(
    pool, symbol: str, as_of: date, fundamental_score: float | None,
    technical_score: float | None, composite_score: float | None,
    regime: str | None, regime_confidence: float | None,
    used_fundamentals: bool, details: dict[str, Any] | None = None,
) -> int:
    # / upsert analysis_scores row, returns id
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO analysis_scores (symbol, date, fundamental_score, technical_score,
                composite_score, regime, regime_confidence, used_fundamentals, details)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (symbol, date) DO UPDATE SET
                fundamental_score = EXCLUDED.fundamental_score,
                technical_score = EXCLUDED.technical_score,
                composite_score = EXCLUDED.composite_score,
                regime = EXCLUDED.regime,
                regime_confidence = EXCLUDED.regime_confidence,
                used_fundamentals = EXCLUDED.used_fundamentals,
                details = COALESCE(analysis_scores.details, '{}'::jsonb) || COALESCE(EXCLUDED.details, '{}'::jsonb)
            RETURNING id
            """,
            symbol, as_of,
            Decimal(str(fundamental_score)) if fundamental_score is not None else None,
            Decimal(str(technical_score)) if technical_score is not None else None,
            Decimal(str(composite_score)) if composite_score is not None else None,
            regime, Decimal(str(regime_confidence)) if regime_confidence is not None else None,
            used_fundamentals,
            details if details else None,
        )
        return row["id"]


async def fetch_analysis_score(pool, symbol: str, as_of: date | None = None) -> dict | None:
    # / get latest analysis_scores row for symbol
    as_of = as_of or date.today()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM analysis_scores
            WHERE symbol = $1 AND date <= $2
            ORDER BY date DESC LIMIT 1""",
            symbol, as_of,
        )
    return dict(row) if row else None


async def store_trade_signal(
    pool, strategy_id: str, symbol: str, signal_type: str,
    strength: float, regime: str | None, details: dict | None = None,
) -> int:
    # / upsert trade signal — one pending per strategy+symbol+type per day
    async with pool.acquire() as conn:
        # / check for existing pending signal today
        existing = await conn.fetchrow(
            """SELECT id FROM trade_signals
            WHERE strategy_id = $1 AND symbol = $2 AND signal_type = $3
            AND created_at::date = CURRENT_DATE AND status = 'pending'""",
            strategy_id, symbol, signal_type,
        )
        if existing:
            await conn.execute(
                """UPDATE trade_signals SET strength = $1, regime = $2, details = $3
                WHERE id = $4""",
                Decimal(str(strength)), regime,
                details if details else None, existing["id"],
            )
            return existing["id"]
        row = await conn.fetchrow(
            """INSERT INTO trade_signals (strategy_id, symbol, signal_type, strength, regime, details, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'pending')
            RETURNING id""",
            strategy_id, symbol, signal_type,
            Decimal(str(strength)), regime,
            details if details else None,
        )
        return row["id"]


async def fetch_pending_signals(pool, limit: int = 50) -> list[dict]:
    # / get unprocessed trade signals
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM trade_signals
            WHERE status = 'pending'
            ORDER BY created_at ASC LIMIT $1""",
            limit,
        )
    return [dict(r) for r in rows]


async def store_approved_trade(
    pool, signal_id: int, symbol: str, side: str, qty: float,
    order_type: str = "market", strategy_id: str | None = None,
) -> int:
    # / insert approved trade
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO approved_trades (signal_id, symbol, side, qty, order_type, status, strategy_id)
            VALUES ($1, $2, $3, $4, $5, 'pending', $6)
            RETURNING id""",
            signal_id, symbol, side,
            Decimal(str(qty)), order_type, strategy_id,
        )
        return row["id"]


async def fetch_pending_trades(pool, limit: int = 50) -> list[dict]:
    # / get unexecuted approved trades
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM approved_trades
            WHERE status = 'pending'
            ORDER BY created_at ASC LIMIT $1""",
            limit,
        )
    return [dict(r) for r in rows]


async def update_trade_status(pool, table: str, row_id: int, status: str) -> bool:
    # / update status column on trade_signals or approved_trades
    if table not in _STATUS_TABLES:
        raise ValueError(f"invalid table '{table}', must be one of {_STATUS_TABLES}")
    async with pool.acquire() as conn:
        result = await conn.execute(
            f"UPDATE {table} SET status = $1 WHERE id = $2",
            status, row_id,
        )
        return result == "UPDATE 1"


async def store_trade_log(
    pool, trade_id: int | None, symbol: str, side: str, qty: float,
    price: float, order_id: str | None, broker: str | None,
    regime: str | None, pnl: float | None,
    strategy_id: str | None = None, details: dict | None = None,
) -> int:
    # / log executed trade
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO trade_log (trade_id, symbol, side, qty, price, order_id, broker, regime, pnl, strategy_id, details)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id""",
            trade_id, symbol, side,
            Decimal(str(qty)), Decimal(str(price)),
            order_id, broker, regime,
            Decimal(str(pnl)) if pnl is not None else None,
            strategy_id,
            details if details else None,
        )
        return row["id"]


async def open_strategy_position(
    pool, strategy_id: str, symbol: str, qty: float, price: float,
) -> None:
    # / upsert strategy position — average entry price on add
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO strategy_positions (strategy_id, symbol, qty, avg_entry_price, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (strategy_id, symbol) DO UPDATE SET
                avg_entry_price = (
                    strategy_positions.avg_entry_price * strategy_positions.qty
                    + EXCLUDED.avg_entry_price * EXCLUDED.qty
                ) / NULLIF(strategy_positions.qty + EXCLUDED.qty, 0),
                qty = strategy_positions.qty + EXCLUDED.qty,
                updated_at = NOW()""",
            strategy_id, symbol, Decimal(str(qty)), Decimal(str(price)),
        )
    logger.info("strategy_position_opened", strategy_id=strategy_id, symbol=symbol, qty=qty, price=price)


async def close_strategy_position(
    pool, strategy_id: str, symbol: str, qty: float,
) -> float | None:
    # / atomic reduce qty, delete if zero. returns entry_price for pnl calc
    async with pool.acquire() as conn:
        # / atomic: lock row, read, update/delete in one transaction
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT qty, avg_entry_price FROM strategy_positions WHERE strategy_id = $1 AND symbol = $2 FOR UPDATE",
                strategy_id, symbol,
            )
            if not row:
                logger.warning("close_position_not_found", strategy_id=strategy_id, symbol=symbol)
                return None

            entry_price = float(row["avg_entry_price"]) if row["avg_entry_price"] else None
            remaining = float(row["qty"]) - qty

            if remaining <= 0:
                await conn.execute(
                    "DELETE FROM strategy_positions WHERE strategy_id = $1 AND symbol = $2",
                    strategy_id, symbol,
                )
            else:
                await conn.execute(
                    "UPDATE strategy_positions SET qty = $1, updated_at = NOW() WHERE strategy_id = $2 AND symbol = $3",
                    Decimal(str(remaining)), strategy_id, symbol,
                )
    logger.info("strategy_position_closed", strategy_id=strategy_id, symbol=symbol, qty=qty, remaining=max(0, remaining))
    return entry_price


async def get_strategy_positions(
    pool, strategy_id: str | None = None, symbol: str | None = None,
) -> list[dict]:
    # / query strategy positions with optional filters
    async with pool.acquire() as conn:
        if strategy_id and symbol:
            rows = await conn.fetch(
                "SELECT strategy_id, symbol, qty, avg_entry_price FROM strategy_positions WHERE strategy_id = $1 AND symbol = $2",
                strategy_id, symbol,
            )
        elif strategy_id:
            rows = await conn.fetch(
                "SELECT strategy_id, symbol, qty, avg_entry_price FROM strategy_positions WHERE strategy_id = $1",
                strategy_id,
            )
        elif symbol:
            rows = await conn.fetch(
                "SELECT strategy_id, symbol, qty, avg_entry_price FROM strategy_positions WHERE symbol = $1",
                symbol,
            )
        else:
            rows = await conn.fetch(
                "SELECT strategy_id, symbol, qty, avg_entry_price FROM strategy_positions",
            )
    return [{"strategy_id": r["strategy_id"], "symbol": r["symbol"],
             "qty": float(r["qty"]), "avg_entry_price": float(r["avg_entry_price"]) if r["avg_entry_price"] else None}
            for r in rows]


async def sync_strategy_positions_from_alpaca(pool) -> int:
    # / bootstrap: insert untracked positions from alpaca not in strategy_positions
    from src.data.alpaca_client import alpaca_base_url, alpaca_headers, get_alpaca_client
    base = alpaca_base_url()
    headers = alpaca_headers()
    try:
        client = await get_alpaca_client()
        resp = await client.get(f"{base}/v2/positions", headers=headers)
        resp.raise_for_status()
        alpaca_positions = resp.json()
    except Exception as exc:
        logger.warning("alpaca_position_sync_failed", error=str(exc))
        return 0

    # / get sum of tracked qty per symbol
    async with pool.acquire() as conn:
        tracked = await conn.fetch(
            "SELECT symbol, SUM(qty) as total_qty FROM strategy_positions GROUP BY symbol",
        )
    tracked_map = {r["symbol"]: float(r["total_qty"]) for r in tracked}

    synced = 0
    for p in alpaca_positions:
        symbol = p["symbol"]
        alpaca_qty = float(p.get("qty", 0))
        if alpaca_qty <= 0:
            continue
        tracked_qty = tracked_map.get(symbol, 0)
        untracked_qty = alpaca_qty - tracked_qty
        if untracked_qty > 0.0001:
            avg_price = float(p.get("avg_entry_price", 0))
            await open_strategy_position(pool, "untracked", symbol, untracked_qty, avg_price)
            synced += 1

    if synced:
        logger.info("strategy_positions_synced", untracked_inserted=synced)
    return synced


async def sync_trades_from_alpaca(pool) -> int:
    # / pull filled orders from alpaca and upsert into trade_log
    # / alpaca is source of truth — this keeps db in sync after restarts
    from src.data.alpaca_client import alpaca_base_url, alpaca_headers, get_alpaca_client
    base = alpaca_base_url()
    headers = alpaca_headers()
    try:
        client = await get_alpaca_client()
        resp = await client.get(
            f"{base}/v2/orders",
            headers=headers,
            params={"status": "filled", "limit": 200, "direction": "desc"},
        )
        resp.raise_for_status()
        orders = resp.json()
    except Exception as exc:
        logger.warning("alpaca_sync_fetch_failed", error=str(exc))
        return 0

    synced = 0
    async with pool.acquire() as conn:
        for o in orders:
            order_id = o["id"]
            # / skip if already in trade_log
            existing = await conn.fetchrow(
                "SELECT id FROM trade_log WHERE order_id = $1", order_id,
            )
            if existing:
                continue
            symbol = o["symbol"]
            side = o["side"]
            qty = float(o.get("filled_qty", 0))
            price = float(o.get("filled_avg_price", 0))
            filled_at = o.get("filled_at")
            if qty <= 0 or price <= 0:
                continue
            await conn.execute(
                """INSERT INTO trade_log
                (trade_id, symbol, side, qty, price, order_id, broker, regime, pnl, strategy_id, details, created_at)
                VALUES (NULL, $1, $2, $3, $4, $5, 'AlpacaBroker', NULL, NULL, NULL,
                        $6, $7::timestamptz)""",
                symbol, side, Decimal(str(qty)), Decimal(str(price)),
                order_id, {"order_type": o.get("type"), "time_in_force": o.get("time_in_force")},
                filled_at,
            )
            synced += 1
    if synced:
        logger.info("alpaca_sync_complete", synced=synced, total_orders=len(orders))
    return synced


async def store_strategy_score(
    pool, strategy_id: str, period_start: date, period_end: date,
    sharpe_ratio: float, max_drawdown: float, win_rate: float,
    brier_score: float | None, total_trades: int,
    regime_breakdown: dict | None = None,
) -> int:
    # / store backtest/live performance score
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO strategy_scores
            (strategy_id, period_start, period_end, sharpe_ratio, max_drawdown,
             win_rate, brier_score, total_trades, regime_breakdown)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id""",
            strategy_id, period_start, period_end,
            Decimal(str(sharpe_ratio)), Decimal(str(max_drawdown)),
            Decimal(str(win_rate)),
            Decimal(str(brier_score)) if brier_score is not None else None,
            total_trades,
            regime_breakdown if regime_breakdown else None,
        )
        return row["id"]


async def fetch_strategy_scores(pool) -> list[dict]:
    # / get all strategy scores, most recent first
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM strategy_scores ORDER BY created_at DESC"
        )
    return [dict(r) for r in rows]


async def store_evolution_log(
    pool, generation: int, action: str, strategy_id: str,
    parent_id: str | None, reason: str, details: dict | None = None,
) -> int:
    # / log evolution action (kill, mutate, promote, demote)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO evolution_log (generation, action, strategy_id, parent_id, reason, details)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id""",
            generation, action, strategy_id, parent_id, reason,
            details if details else None,
        )
        return row["id"]


async def fetch_recent_trades(pool, strategy_id: str | None = None, limit: int = 100) -> list[dict]:
    # / get recent trade log entries, optionally filtered by strategy
    async with pool.acquire() as conn:
        if strategy_id:
            rows = await conn.fetch(
                """SELECT * FROM trade_log
                WHERE strategy_id = $1
                ORDER BY created_at DESC LIMIT $2""",
                strategy_id, limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM trade_log ORDER BY created_at DESC LIMIT $1",
                limit,
            )
    return [dict(r) for r in rows]


def dict_to_analysis_data(d: dict) -> AnalysisData:
    # / deserialize dict from JSONB to AnalysisData
    return AnalysisData(
        pe_ratio=d.get("pe_ratio"),
        pe_forward=d.get("pe_forward"),
        ps_ratio=d.get("ps_ratio"),
        peg_ratio=d.get("peg_ratio"),
        revenue_growth=d.get("revenue_growth"),
        fcf_margin=d.get("fcf_margin"),
        debt_to_equity=d.get("debt_to_equity"),
        sector_pe_avg=d.get("sector_pe_avg"),
        sector_ps_avg=d.get("sector_ps_avg"),
        dcf_upside=d.get("dcf_upside"),
        insider_net_buy_ratio=d.get("insider_net_buy_ratio"),
        earnings_surprise_pct=d.get("earnings_surprise_pct"),
        consecutive_beats=d.get("consecutive_beats", 0),
        fundamental_score=d.get("fundamental_score"),
        nvt_ratio=d.get("nvt_ratio"),
        funding_rate=d.get("funding_rate"),
        exchange_flow_ratio=d.get("exchange_flow_ratio"),
        news_sentiment_score=d.get("news_sentiment_score"),
        ai_consensus=d.get("ai_consensus"),
        regime=d.get("regime"),
    )


async def count_symbol_trades(pool, strategy_id: str, symbol: str) -> int:
    # / count trades for a specific strategy + symbol combo
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT COUNT(*) as cnt FROM trade_log
            WHERE strategy_id = $1 AND symbol = $2""",
            strategy_id, symbol,
        )
        return int(row["cnt"]) if row else 0


async def store_daily_synthesis(
    pool, date, model: str, top_buys: list | None,
    top_avoids: list | None, portfolio_risk: str | None,
    per_symbol_notes: dict | None, raw_response: str | None,
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO daily_synthesis (date, model, top_buys, top_avoids,
                portfolio_risk, per_symbol_notes, raw_response)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (date) DO UPDATE SET
                model = EXCLUDED.model, top_buys = EXCLUDED.top_buys,
                top_avoids = EXCLUDED.top_avoids, portfolio_risk = EXCLUDED.portfolio_risk,
                per_symbol_notes = EXCLUDED.per_symbol_notes, raw_response = EXCLUDED.raw_response
            RETURNING id""",
            date, model,
            top_buys if top_buys else None,
            top_avoids if top_avoids else None,
            portfolio_risk,
            per_symbol_notes if per_symbol_notes else None,
            raw_response,
        )
        return row["id"]


async def fetch_daily_synthesis(pool, target_date=None) -> dict | None:
    async with pool.acquire() as conn:
        if target_date:
            row = await conn.fetchrow(
                "SELECT * FROM daily_synthesis WHERE date = $1", target_date,
            )
        else:
            row = await conn.fetchrow(
                "SELECT * FROM daily_synthesis ORDER BY date DESC LIMIT 1",
            )
        return dict(row) if row else None


async def count_all_symbol_trades(pool, symbol: str) -> int:
    # / count ALL trades for a symbol across all strategies
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT COUNT(*) as cnt FROM trade_log
            WHERE symbol = $1""",
            symbol,
        )
        return int(row["cnt"]) if row else 0


async def store_strategy_evaluation(pool, stats: dict[str, Any]) -> int | None:
    # / persist strategy evaluation cycle stats for dashboard
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO strategy_evaluations
                (total_pairs, entry_hits, blocked_consensus, blocked_threshold,
                 signals_generated, strategies_evaluated, near_misses)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id""",
                stats.get("total", 0),
                stats.get("total", 0) - stats.get("no_entry", 0) - stats.get("insufficient_data", 0),
                stats.get("blocked_consensus", 0),
                stats.get("blocked_threshold", 0),
                stats.get("signals", 0),
                stats.get("strategies_evaluated", 0),
                stats.get("near_misses", []),
            )
            return row["id"] if row else None
    except Exception as exc:
        logger.warning("store_strategy_evaluation_failed", error=str(exc))
        return None


async def store_computed_indicators(
    pool, symbol: str, indicators: dict[str, Any], timeframe: str = "1Day",
) -> None:
    # / upsert latest indicator values for dashboard
    from datetime import date as dt_date
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO computed_indicators
                (symbol, date, timeframe, rsi14, macd, macd_signal, macd_histogram,
                 adx, sma20, sma50, bb_upper, bb_middle, bb_lower, atr)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                ON CONFLICT (symbol, date, timeframe) DO UPDATE SET
                    rsi14 = EXCLUDED.rsi14, macd = EXCLUDED.macd,
                    macd_signal = EXCLUDED.macd_signal, macd_histogram = EXCLUDED.macd_histogram,
                    adx = EXCLUDED.adx, sma20 = EXCLUDED.sma20, sma50 = EXCLUDED.sma50,
                    bb_upper = EXCLUDED.bb_upper, bb_middle = EXCLUDED.bb_middle,
                    bb_lower = EXCLUDED.bb_lower, atr = EXCLUDED.atr""",
                symbol, dt_date.today(), timeframe,
                indicators.get("rsi14"), indicators.get("macd"),
                indicators.get("macd_signal"), indicators.get("macd_histogram"),
                indicators.get("adx"), indicators.get("sma20"), indicators.get("sma50"),
                indicators.get("bb_upper"), indicators.get("bb_middle"),
                indicators.get("bb_lower"), indicators.get("atr"),
            )
    except Exception as exc:
        logger.warning("store_indicators_failed", symbol=symbol, error=str(exc))


async def fetch_latest_regime(pool, market: str = "equity") -> str | None:
    # / get latest regime from regime_history for equity or crypto
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT regime FROM regime_history
                WHERE market = $1 ORDER BY date DESC LIMIT 1""",
                market,
            )
            return row["regime"] if row else None
    except Exception:
        return None


async def log_event(
    pool, level: str, source: str, message: str,
    symbol: str | None = None, details: dict | None = None,
) -> None:
    # / fire-and-forget event log — never blocks pipeline
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO system_events (level, source, symbol, message, details)
                VALUES ($1, $2, $3, $4, $5::jsonb)""",
                level, source, symbol, message,
                json.dumps(details) if details else None,
            )
    except Exception as exc:
        logger.warning("log_event_failed", source=source, error=str(exc))
