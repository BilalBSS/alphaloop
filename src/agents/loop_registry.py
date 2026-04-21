# / phase 6 step 1: loop introspection registry
# / orchestrator loops call record_fire_start / record_fire_end on every cycle;
# / the dashboard reads describe_loops() via /api/loops. trigger_requests is a
# / cross-process command queue for /api/admin/trigger/{service}.

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


# / canonical metadata per loop — name -> kind + cadence/cron hour
# / interval loops declare cadence_seconds; cron loops declare cron_hour_et (ET)
LOOP_METADATA: dict[str, dict[str, Any]] = {
    "analyst":              {"kind": "interval", "cadence_seconds": 1200, "description": "groq analyst pass (batched, staleness-ordered)"},
    "deepseek":             {"kind": "interval", "cadence_seconds": 1800, "description": "deepseek dual-llm pass (batched, staleness-ordered)"},
    "reasoner":             {"kind": "cron",     "cron_hour_et": 17,     "description": "daily synthesis (deepseek-reasoner)"},
    "strategy":             {"kind": "interval", "cadence_seconds": 300,  "description": "strategy evaluation cycle"},
    "risk":                 {"kind": "interval", "cadence_seconds": 5,    "description": "risk signal poll"},
    "executor":             {"kind": "interval", "cadence_seconds": 5,    "description": "approved trade executor poll"},
    "evolution":            {"kind": "cron",     "cron_hour_et": 0,       "description": "nightly strategy evolution"},
    "insider_backfill":     {"kind": "cron",     "cron_hour_et": 6,       "description": "sec form 4 insider trades"},
    "fundamentals_backfill":{"kind": "cron",     "cron_hour_et": 7,       "description": "edgar/finnhub/yf fundamentals"},
    "crypto_backfill":      {"kind": "interval", "cadence_seconds": 1800, "description": "crypto ohlcv + coingecko"},
    "intraday_backfill":    {"kind": "interval", "cadence_seconds": 3600, "description": "1h/2h intraday bars"},
    "daily_bar_backfill":   {"kind": "interval", "cadence_seconds": 14400,"description": "daily equity ohlcv"},
    "price_refresh":        {"kind": "interval", "cadence_seconds": 300,  "description": "latest price snapshot (market hours)"},
    "alpaca_sync":          {"kind": "interval", "cadence_seconds": 300,  "description": "alpaca trade + position reconciliation"},
    "strategy_metrics":     {"kind": "interval", "cadence_seconds": 3600, "description": "live sharpe/sortino/drawdown"},
    "alternative_data":     {"kind": "interval", "cadence_seconds": 86400,"description": "analyst ratings, short, dark pool, options"},
    "monitoring":           {"kind": "interval", "cadence_seconds": 3600, "description": "staleness + decay + correlation checks"},
    "cost_flush":           {"kind": "interval", "cadence_seconds": 3600, "description": "llm cost tracker flush"},
    "macro_backfill":       {"kind": "cron",     "cron_hour_et": 9,       "description": "fred macro indicators"},
    "alert":                {"kind": "interval", "cadence_seconds": 30,   "description": "price-cross alert scanner"},
    "regime_backfill":      {"kind": "interval", "cadence_seconds": 21600,"description": "equity + crypto + sector regime"},
    "wiki_embedding":       {"kind": "interval", "cadence_seconds": 21600,"description": "ollama embedding backfill"},
    "wiki_archive":         {"kind": "interval", "cadence_seconds": 86400,"description": "archive wiki docs > 180d"},
    "knowledge_hydration":  {"kind": "interval", "cadence_seconds": 86400,"description": "daily symbol wiki enrichment"},
    "capital_allocator":    {"kind": "interval", "cadence_seconds": 604800,"description": "weekly kelly-weighted strategy allocation refresh"},
}

ALL_LOOP_NAMES: list[str] = list(LOOP_METADATA.keys())


# / per-loop cycle-body timeout (seconds). wedged loops don't block the next tick
# / forever — asyncio.wait_for raises TimeoutError, the tracker records an error,
# / the next interval re-attempts. only set for loops that historically wedged.
LOOP_TIMEOUTS: dict[str, float] = {
    # / hard ceiling — run() has its own wall_clock_budget_s (shorter, see
    # / orchestrator.ANALYST_BUDGET_S). timeout only fires if one batch truly
    # / hangs past the budget + some slack.
    "analyst":         720.0,   # / 12 min (budget 7 min + 5 slack)
    "deepseek":        780.0,   # / 13 min (budget 8 min + 5 slack)
    "wiki_embedding":  900.0,   # / 15 min — ollama backfill batches
    "wiki_archive":    900.0,   # / 15 min — archive + rewrite old docs
}


def timeout_for(name: str) -> float | None:
    # / returns cycle-body timeout for `name`, or None (no enforced timeout).
    return LOOP_TIMEOUTS.get(name)


def _compute_next_fire(meta: dict[str, Any], base: datetime | None = None) -> datetime | None:
    # / project the next fire from now + cadence (interval) or next et cron hour
    now = base or datetime.now(timezone.utc)
    if meta.get("kind") == "cron":
        hour = int(meta.get("cron_hour_et", 0))
        try:
            from zoneinfo import ZoneInfo
            et = ZoneInfo("America/New_York")
        except Exception:
            et = timezone(timedelta(hours=-5))
        et_now = now.astimezone(et)
        target = et_now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if et_now >= target:
            target += timedelta(days=1)
        return target.astimezone(timezone.utc)
    cadence = meta.get("cadence_seconds")
    if cadence:
        return now + timedelta(seconds=int(cadence))
    return None


async def record_fire_start(pool: asyncpg.Pool | None, name: str) -> None:
    # / set last_status=running so the dashboard can show in-flight loops.
    # / clears last_error so the previous cycle's failure message doesn't bleed
    # / through on /api/loops while the new cycle is mid-flight.
    if pool is None:
        return
    meta = LOOP_METADATA.get(name, {})
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO loop_activity (name, kind, cadence_seconds, cron_hour_et, last_status, last_error, updated_at)
                VALUES ($1, $2, $3, $4, 'running', NULL, NOW())
                ON CONFLICT (name) DO UPDATE SET
                    last_status = 'running',
                    last_error = NULL,
                    updated_at = NOW()
                """,
                name,
                meta.get("kind", "interval"),
                meta.get("cadence_seconds"),
                meta.get("cron_hour_et"),
            )
    except Exception as exc:
        logger.debug("loop_registry_start_failed", name=name, error=str(exc)[:120])


async def is_loop_running(pool: asyncpg.Pool | None, name: str) -> bool:
    # / cross-process check for the "already in-flight" trigger guard. returns
    # / True when loop_activity has last_status='running' for this service.
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_status FROM loop_activity WHERE name=$1",
                name,
            )
    except Exception as exc:
        logger.debug("is_loop_running_failed", name=name, error=str(exc)[:120])
        return False
    return bool(row and row["last_status"] == "running")


async def upsert_service_state(
    pool: asyncpg.Pool | None,
    name: str,
    status: str,
    error: str | None = None,
    duration_ms: int = 0,
) -> None:
    # / write a synthetic "service state" row into loop_activity for components
    # / that aren't loops but still want their status surfaced on /api/loops and
    # / consumed by the dashboard without cross-process module-global dependence.
    # / used by kronos HF load success/failure so the dashboard reads ground truth.
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO loop_activity (
                    name, kind, last_fire_ts, last_duration_ms, last_status, last_error, updated_at
                ) VALUES ($1, 'service', NOW(), $2, $3, $4, NOW())
                ON CONFLICT (name) DO UPDATE SET
                    last_fire_ts = NOW(),
                    last_duration_ms = EXCLUDED.last_duration_ms,
                    last_status = EXCLUDED.last_status,
                    last_error = EXCLUDED.last_error,
                    updated_at = NOW()
                """,
                name,
                int(duration_ms),
                status,
                (str(error)[:500] if error else None),
            )
    except Exception as exc:
        logger.debug("upsert_service_state_failed", name=name, error=str(exc)[:120])


async def fetch_service_state(
    pool: asyncpg.Pool | None,
    name: str,
) -> dict[str, Any] | None:
    # / returns the latest status row for a named service (or None).
    # / dashboard reads via this to avoid importing process-local module globals.
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name, last_status, last_error, last_fire_ts, last_duration_ms, updated_at "
                "FROM loop_activity WHERE name=$1",
                name,
            )
    except Exception as exc:
        logger.debug("fetch_service_state_failed", name=name, error=str(exc)[:120])
        return None
    return dict(row) if row else None


async def record_fire_end(
    pool: asyncpg.Pool | None,
    name: str,
    status: str,
    duration_ms: int,
    error: str | None = None,
) -> None:
    # / persist outcome + project next fire time based on the loop's metadata
    if pool is None:
        return
    meta = LOOP_METADATA.get(name, {})
    next_fire = _compute_next_fire(meta)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO loop_activity (
                    name, kind, cadence_seconds, cron_hour_et,
                    last_fire_ts, last_duration_ms, last_status, last_error, next_fire_ts, updated_at
                ) VALUES ($1, $2, $3, $4, NOW(), $5, $6, $7, $8, NOW())
                ON CONFLICT (name) DO UPDATE SET
                    last_fire_ts = NOW(),
                    last_duration_ms = EXCLUDED.last_duration_ms,
                    last_status = EXCLUDED.last_status,
                    last_error = EXCLUDED.last_error,
                    next_fire_ts = EXCLUDED.next_fire_ts,
                    updated_at = NOW()
                """,
                name,
                meta.get("kind", "interval"),
                meta.get("cadence_seconds"),
                meta.get("cron_hour_et"),
                int(duration_ms),
                status,
                (error or None) if error is None else str(error)[:500],
                next_fire,
            )
    except Exception as exc:
        logger.debug("loop_registry_end_failed", name=name, error=str(exc)[:120])


async def describe_loops(pool: asyncpg.Pool | None) -> list[dict[str, Any]]:
    # / returns one row per known loop, merged with its metadata so newly-added
    # / loops that haven't fired yet still surface with 'never' timestamps
    rows_by_name: dict[str, dict] = {}
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM loop_activity")
                rows_by_name = {r["name"]: dict(r) for r in rows}
        except Exception as exc:
            logger.debug("describe_loops_query_failed", error=str(exc)[:120])

    out: list[dict[str, Any]] = []
    for name, meta in LOOP_METADATA.items():
        row = rows_by_name.get(name, {})
        out.append({
            "name": name,
            "kind": meta["kind"],
            "cadence_seconds": meta.get("cadence_seconds"),
            "cron_hour_et": meta.get("cron_hour_et"),
            "description": meta.get("description", ""),
            "last_fire_ts": row.get("last_fire_ts"),
            "last_duration_ms": row.get("last_duration_ms"),
            "last_status": row.get("last_status"),
            "last_error": row.get("last_error"),
            "next_fire_ts": row.get("next_fire_ts") or _compute_next_fire(meta),
        })
    out.sort(key=lambda r: r["name"])
    return out


async def enqueue_trigger(pool: asyncpg.Pool | None, service: str) -> int | None:
    # / insert a pending trigger request; returns row id or None on failure
    # / rate-limit: reject if an unclaimed request for the same service already exists
    if pool is None or service not in LOOP_METADATA:
        return None
    try:
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT id FROM trigger_requests WHERE service=$1 AND status='pending'",
                service,
            )
            if existing:
                return int(existing)
            row = await conn.fetchrow(
                "INSERT INTO trigger_requests (service) VALUES ($1) RETURNING id",
                service,
            )
            return int(row["id"]) if row else None
    except Exception as exc:
        logger.warning("enqueue_trigger_failed", service=service, error=str(exc)[:120])
        return None


async def claim_pending_triggers(pool: asyncpg.Pool | None) -> list[tuple[int, str]]:
    # / called by orchestrator poll loop; returns [(id, service), ...] and marks them running
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE trigger_requests
                SET status='running', claimed_at=NOW()
                WHERE id IN (
                    SELECT id FROM trigger_requests
                    WHERE status='pending'
                    ORDER BY requested_at ASC
                    LIMIT 20
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, service
                """,
            )
            return [(int(r["id"]), r["service"]) for r in rows]
    except Exception as exc:
        logger.warning("claim_triggers_failed", error=str(exc)[:120])
        return []


async def complete_trigger(
    pool: asyncpg.Pool | None,
    trigger_id: int,
    status: str,
    error: str | None = None,
) -> None:
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE trigger_requests
                SET status=$2, completed_at=NOW(), error=$3
                WHERE id=$1""",
                trigger_id, status, (str(error)[:500] if error else None),
            )
    except Exception as exc:
        logger.debug("complete_trigger_failed", trigger_id=trigger_id, error=str(exc)[:120])


class _LoopTracker:
    # / async context manager that times a loop body and records start/end
    __slots__ = ("_pool", "_name", "_started")

    def __init__(self, pool: asyncpg.Pool | None, name: str) -> None:
        self._pool = pool
        self._name = name
        self._started = 0.0

    async def __aenter__(self) -> "_LoopTracker":
        import time
        self._started = time.monotonic()
        await record_fire_start(self._pool, self._name)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        import asyncio as _asyncio
        import time
        duration_ms = int((time.monotonic() - self._started) * 1000)
        # / CancelledError is a normal shutdown signal (orchestrator.stop), not a bug.
        # / marking it as "error" pollutes the System tab's error count on every restart.
        if exc is None:
            status = "ok"
            err_msg = None
        elif exc_type is not None and issubclass(exc_type, _asyncio.CancelledError):
            status = "cancelled"
            err_msg = None
        else:
            status = "error"
            err_msg = f"{exc_type.__name__ if exc_type else 'Exception'}: {exc}"
        await record_fire_end(self._pool, self._name, status, duration_ms, err_msg)
        return False  # / don't swallow exceptions


def track(pool: asyncpg.Pool | None, name: str) -> _LoopTracker:
    # / usage: `async with track(pool, "macro_backfill"):` inside a loop body
    return _LoopTracker(pool, name)
