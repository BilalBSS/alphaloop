
from __future__ import annotations

from decimal import Decimal

import structlog

logger = structlog.get_logger(__name__)


async def open_strategy_position(
    pool, strategy_id: str, symbol: str, qty: float, price: float,
) -> None:
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
    async with pool.acquire() as conn, conn.transaction():
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


async def fetch_most_recent_open_entry(pool, symbol: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT price, qty, strategy_id, created_at
            FROM trade_log
            WHERE symbol = $1 AND side = 'buy'
            ORDER BY created_at DESC LIMIT 1""",
            symbol,
        )
    if not row:
        return None
    return {
        "entry_price": float(row["price"]) if row["price"] else None,
        "qty": float(row["qty"]) if row["qty"] else 0.0,
        "strategy_id": row["strategy_id"],
        "created_at": row["created_at"],
    }


async def get_strategy_positions(
    pool, strategy_id: str | None = None, symbol: str | None = None,
) -> list[dict]:
    async with pool.acquire() as conn:
        if strategy_id and symbol:
            rows = await conn.fetch(
                "SELECT strategy_id, symbol, qty, avg_entry_price, updated_at, COALESCE(partial_exit_fired, FALSE) AS partial_exit_fired FROM strategy_positions WHERE strategy_id = $1 AND symbol = $2",
                strategy_id, symbol,
            )
        elif strategy_id:
            rows = await conn.fetch(
                "SELECT strategy_id, symbol, qty, avg_entry_price, updated_at, COALESCE(partial_exit_fired, FALSE) AS partial_exit_fired FROM strategy_positions WHERE strategy_id = $1",
                strategy_id,
            )
        elif symbol:
            rows = await conn.fetch(
                "SELECT strategy_id, symbol, qty, avg_entry_price, updated_at, COALESCE(partial_exit_fired, FALSE) AS partial_exit_fired FROM strategy_positions WHERE symbol = $1",
                symbol,
            )
        else:
            rows = await conn.fetch(
                "SELECT strategy_id, symbol, qty, avg_entry_price, updated_at, COALESCE(partial_exit_fired, FALSE) AS partial_exit_fired FROM strategy_positions",
            )
    return [{"strategy_id": r["strategy_id"], "symbol": r["symbol"],
             "qty": float(r["qty"]), "avg_entry_price": float(r["avg_entry_price"]) if r["avg_entry_price"] else None,
             "updated_at": r["updated_at"],
             "partial_exit_fired": bool(r.get("partial_exit_fired", False)) if hasattr(r, "get") else bool(r["partial_exit_fired"])}
            for r in rows]


async def reconcile_strategy_positions(
    pool, alpaca_map: dict[str, float], full_sync: bool = False,
    price_map: dict[str, float] | None = None,
) -> None:
    price_map = price_map or {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, strategy_id, symbol, qty FROM strategy_positions",
        )

        if not alpaca_map and rows and not full_sync:
            logger.warning("reconcile_skipped_empty_alpaca", tracked=len(rows))
            return

        db_by_symbol: dict[str, list[dict]] = {}
        for r in rows:
            s = r["symbol"]
            db_by_symbol.setdefault(s, []).append(dict(r))

        async with conn.transaction():
            for symbol, alpaca_qty in alpaca_map.items():
                entries = db_by_symbol.pop(symbol, [])
                if not entries:
                    avg_price = price_map.get(symbol, 0)
                    recovered = await conn.fetchrow(
                        """SELECT strategy_id FROM trade_log
                        WHERE symbol = $1 AND side = 'buy' AND strategy_id IS NOT NULL
                        ORDER BY created_at DESC LIMIT 1""",
                        symbol,
                    )
                    strat = (recovered["strategy_id"] if recovered else None) or "untracked"
                    await conn.execute(
                        """INSERT INTO strategy_positions (strategy_id, symbol, qty, avg_entry_price, updated_at)
                        VALUES ($1, $2, $3, $4, NOW())
                        ON CONFLICT (strategy_id, symbol) DO UPDATE SET
                            qty = $3,
                            avg_entry_price = COALESCE(NULLIF($4, 0), strategy_positions.avg_entry_price),
                            updated_at = NOW()""",
                        strat, symbol, Decimal(str(alpaca_qty)), Decimal(str(avg_price or 0)),
                    )
                    continue

                total_tracked = sum(float(e["qty"]) for e in entries)
                if abs(total_tracked - alpaca_qty) < 0.0001:
                    untracked_rows = [e for e in entries if e["strategy_id"] == "untracked"]
                    has_attributed = any(e["strategy_id"] != "untracked" for e in entries)
                    if untracked_rows and not has_attributed:
                        recovered = await conn.fetchrow(
                            """SELECT strategy_id FROM trade_log
                            WHERE symbol = $1 AND side = 'buy' AND strategy_id IS NOT NULL
                            ORDER BY created_at DESC LIMIT 1""",
                            symbol,
                        )
                        if recovered and recovered["strategy_id"]:
                            sid = recovered["strategy_id"]
                            row = untracked_rows[0]
                            await conn.execute(
                                "DELETE FROM strategy_positions WHERE id = $1",
                                row["id"],
                            )
                            await conn.execute(
                                """INSERT INTO strategy_positions
                                (strategy_id, symbol, qty, avg_entry_price, updated_at)
                                VALUES ($1, $2, $3, $4, NOW())
                                ON CONFLICT (strategy_id, symbol) DO UPDATE SET
                                    qty = EXCLUDED.qty,
                                    avg_entry_price = EXCLUDED.avg_entry_price,
                                    updated_at = NOW()""",
                                sid, symbol,
                                Decimal(str(alpaca_qty)),
                                Decimal(str(price_map.get(symbol) or 0)),
                            )
                            logger.info("strategy_position_reattributed",
                                        symbol=symbol, from_id="untracked", to_id=sid)
                    continue

                attributed = [e for e in entries if e["strategy_id"] != "untracked"]
                if attributed:
                    keep = attributed[0]
                    await conn.execute(
                        "UPDATE strategy_positions SET qty = $1, updated_at = NOW() WHERE id = $2",
                        Decimal(str(alpaca_qty)), keep["id"],
                    )
                    remove_ids = [e["id"] for e in entries if e["id"] != keep["id"]]
                else:
                    keep = entries[0]
                    await conn.execute(
                        "UPDATE strategy_positions SET qty = $1, updated_at = NOW() WHERE id = $2",
                        Decimal(str(alpaca_qty)), keep["id"],
                    )
                    remove_ids = [e["id"] for e in entries[1:]]

                if remove_ids:
                    await conn.execute(
                        "DELETE FROM strategy_positions WHERE id = ANY($1::bigint[])", remove_ids,
                    )

            for symbol, entries in db_by_symbol.items():
                ids = [e["id"] for e in entries]
                await conn.execute(
                    "DELETE FROM strategy_positions WHERE id = ANY($1::bigint[])", ids,
                )
                logger.info("position_closed_externally", symbol=symbol)


async def sync_strategy_positions_from_alpaca(pool) -> int:
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


async def mark_partial_exit_fired(
    pool, strategy_id: str, symbol: str,
) -> None:
    # / suppress re-fire next cycle
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE strategy_positions
            SET partial_exit_fired = TRUE, updated_at = NOW()
            WHERE strategy_id=$1 AND symbol=$2""",
            strategy_id, symbol,
        )
