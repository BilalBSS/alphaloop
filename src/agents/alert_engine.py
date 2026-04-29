# / isolated price-cross alert loop — runs in orchestrator process
# / fetches active alerts on a fixed tick, resolves per-symbol prices, fires crossings,
# / pushes to discord + websocket. never shares state with strategy/risk agents.
from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import structlog

from src.dashboard import alerts as alerts_mod
from src.data.resilience import api_post

logger = structlog.get_logger(__name__)

# / default tick, short enough to catch crossings in low-vol regimes without hammering alpaca
DEFAULT_INTERVAL_SEC = 30

# / discord hard cap is ~30 req/min per webhook; we send at most one batched message per tick
_DISCORD_EMBED_LIMIT = 10


def _prev_price_cache() -> dict[str, float]:
    # / module-level cache indirection keeps the loop stateful across ticks without globals leaking into tests
    return {}


async def _resolve_price(broker: Any, symbol: str) -> float | None:
    # / guard against broker returning None / 0 / raising — those are not a cross condition
    try:
        value = await broker.get_price(symbol)
    except Exception as exc:
        logger.debug("alert_price_fetch_failed", symbol=symbol, error=str(exc))
        return None
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return price


def _is_crossed(direction: str, prev: float | None, current: float, target: float) -> bool:
    # / edge-triggered: fires on transition across the target, not on already-past levels
    # / if we have no prev (first observation) we fall back to a level test so cold start still fires
    if direction == alerts_mod.DIRECTION_ABOVE:
        if prev is None:
            return current >= target
        return prev < target <= current
    if direction == alerts_mod.DIRECTION_BELOW:
        if prev is None:
            return current <= target
        return prev > target >= current
    return False


def _build_discord_body(fired: list[dict]) -> dict[str, Any]:
    # / batch all fires this tick into a single message with embeds so we never spam
    # / discord caps at 10 embeds per message, overflow goes into the content line
    overflow = len(fired) - _DISCORD_EMBED_LIMIT
    embeds: list[dict[str, Any]] = []
    for alert in fired[:_DISCORD_EMBED_LIMIT]:
        label = alert.get("label") or ""
        direction = alert.get("direction") or "?"
        symbol = alert.get("symbol") or "?"
        price = alert.get("price")
        current = alert.get("current_price")
        fields = [
            {"name": "target", "value": f"{price}", "inline": True},
            {"name": "direction", "value": direction, "inline": True},
        ]
        if current is not None:
            fields.append({"name": "price", "value": f"{current}", "inline": True})
        if label:
            fields.append({"name": "label", "value": label, "inline": False})
        embeds.append({
            "title": f"alert fired — {symbol}",
            "description": f"price crossed {direction} {price}",
            "color": 0xF59E0B,
            "fields": fields,
        })
    body: dict[str, Any] = {"embeds": embeds}
    if overflow > 0:
        body["content"] = f"+{overflow} more alerts fired"
    return body


async def _send_discord(webhook_url: str, fired: list[dict]) -> bool:
    # / single POST per tick; swallow failures so one bad webhook call never breaks the loop
    # / httpx does not raise on 4xx/5xx by default, so we check the status ourselves
    # / and only log the exception type (never the url) to avoid leaking the webhook into logs
    if not webhook_url or not fired:
        return False
    body = _build_discord_body(fired)
    try:
        resp = await api_post(webhook_url, json=body, timeout=5.0)
        status = getattr(resp, "status_code", None)
        if isinstance(status, int) and status >= 400:
            logger.warning("alert_discord_send_failed", status=status)
            return False
        return True
    except Exception as exc:
        logger.warning("alert_discord_send_failed", error_type=type(exc).__name__)
        return False


async def _broadcast_fires(
    ws_broadcast: Callable[[str, dict], Awaitable[None]] | None,
    fired: list[dict],
) -> None:
    # / fan out each fire as its own ws message so clients can render a toast per alert
    if ws_broadcast is None:
        return
    for alert in fired:
        try:
            await ws_broadcast("alert.triggered", {"alert": alert})
        except Exception as exc:
            logger.debug("alert_ws_broadcast_failed", alert_id=alert.get("id"), error=str(exc))


async def check_and_fire(
    pool: Any,
    broker: Any,
    ws_broadcast: Callable[[str, dict], Awaitable[None]] | None,
    webhook_url: str | None,
    prev_prices: dict[str, float],
) -> list[dict]:
    # / single-tick scan: fetch active alerts, group by symbol, one price fetch per symbol,
    # / check every alert, mark fires atomically, accumulate a batch for discord + ws.
    # / returns the fired list for observability / tests.
    active = await alerts_mod.list_alerts(pool, status=alerts_mod.STATUS_ACTIVE)
    logger.info("alert_check_tick", active_count=len(active))
    if not active:
        return []

    symbols = sorted({a.get("symbol") for a in active if isinstance(a.get("symbol"), str)})
    # / fetch prices in parallel so one tick is one round-trip per broker, not N sequential calls
    price_results = await asyncio.gather(
        *(_resolve_price(broker, sym) for sym in symbols),
        return_exceptions=True,
    )
    prices: dict[str, float] = {}
    for sym, res in zip(symbols, price_results, strict=False):
        if isinstance(res, Exception):
            logger.debug("alert_price_fetch_failed", symbol=sym, error=str(res))
            continue
        if res is not None:
            prices[sym] = res

    fired: list[dict] = []
    scanned_ids: list[int] = []
    now = datetime.now(timezone.utc)

    for alert in active:
        alert_id = alert.get("id")
        if alert_id is None:
            continue
        scanned_ids.append(int(alert_id))
        symbol = alert.get("symbol")
        if symbol not in prices:
            continue
        current = prices[symbol]
        target = alert.get("price")
        direction = alert.get("direction")
        if target is None or not isinstance(direction, str):
            continue
        try:
            target_f = float(target)
        except (TypeError, ValueError):
            continue
        prev = prev_prices.get(symbol)
        try:
            crossed = _is_crossed(direction, prev, current, target_f)
        except Exception as exc:
            logger.warning("alert_check_exception", alert_id=alert_id, error=str(exc))
            continue
        if not crossed:
            continue
        try:
            updated = await alerts_mod.mark_fired(pool, int(alert_id), now)
        except Exception as exc:
            logger.warning("alert_mark_fired_exception", alert_id=alert_id, error=str(exc))
            continue
        if updated is None:
            # / lost the race to another tick / process — already fired
            continue
        updated["current_price"] = current
        fired.append(updated)
        logger.info(
            "alert_fired",
            alert_id=alert_id,
            symbol=symbol,
            price=current,
            target=target_f,
            direction=direction,
        )

    prev_prices.update(prices)

    if scanned_ids:
        try:
            await alerts_mod.mark_checked(pool, scanned_ids, now)
        except Exception as exc:
            logger.debug("alert_mark_checked_failed", count=len(scanned_ids), error=str(exc))

    if fired:
        await _send_discord(webhook_url or "", fired)
        await _broadcast_fires(ws_broadcast, fired)

    return fired


async def alert_loop(
    pool: Any,
    broker: Any,
    ws_broadcast: Callable[[str, dict], Awaitable[None]] | None = None,
    interval_sec: int = DEFAULT_INTERVAL_SEC,
    webhook_url: str | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    # / long-running isolated loop; stop_event is optional so orchestrator can inject its own
    # / never touches strategy/risk state — all side effects go through chart_alerts + ws + webhook
    if webhook_url is None:
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    prev_prices = _prev_price_cache()

    async def _should_stop() -> bool:
        if stop_event is None:
            return False
        return stop_event.is_set()

    while not await _should_stop():
        try:
            await check_and_fire(pool, broker, ws_broadcast, webhook_url, prev_prices)
        except Exception:
            # / swallow loop tick error
            logger.error("alert_loop_error", exc_info=True)
        # / sleep on error too — guarantees even pacing and yields to the event loop
        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
                return
            except asyncio.TimeoutError:
                continue
        await asyncio.sleep(interval_sec)
