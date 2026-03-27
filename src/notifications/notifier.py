# / notification system — discord, slack, telegram webhooks
# / fire-and-forget via asyncio.create_task, never blocks trading pipeline
# / throttle: max 1 per event type per 60s, batch errors

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from src.data.resilience import api_post, get_http_client

logger = structlog.get_logger(__name__)


class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SEVERITY_COLORS = {
    Severity.CRITICAL: 0xFF4757,  # red
    Severity.HIGH: 0xF59E0B,     # orange
    Severity.MEDIUM: 0x3B82F6,   # blue
    Severity.LOW: 0x888888,      # gray
}

SEVERITY_EMOJI = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH: "🟠",
    Severity.MEDIUM: "🔵",
    Severity.LOW: "⚪",
}


@dataclass
class NotificationEvent:
    severity: Severity
    title: str
    message: str
    fields: dict[str, str] = field(default_factory=dict)
    channel: str = "system"  # / discord channel: trades, analysis, strategy, daily, sentiment, system


# / discord channel routing — each maps to a separate webhook
DISCORD_CHANNELS = {
    "trades":    "DISCORD_WEBHOOK_TRADES",
    "analysis":  "DISCORD_WEBHOOK_ANALYSIS",
    "strategy":  "DISCORD_WEBHOOK_STRATEGY",
    "daily":     "DISCORD_WEBHOOK_DAILY",
    "sentiment": "DISCORD_WEBHOOK_SENTIMENT",
    "system":    "DISCORD_WEBHOOK_SYSTEM",
}


# / throttle state — tracks last send time per event key
_last_sent: dict[str, float] = {}
_throttle_seconds: float = 60.0

# / error batch buffer — groups errors by type
_error_buffer: dict[str, list[str]] = {}
_error_flush_task: asyncio.Task | None = None


def _throttle_key(event: NotificationEvent) -> str:
    return f"{event.severity.value}:{event.title}"


def _is_throttled(event: NotificationEvent) -> bool:
    key = _throttle_key(event)
    now = time.monotonic()
    last = _last_sent.get(key, 0)
    if now - last < _throttle_seconds:
        return True
    _last_sent[key] = now
    return False


async def _send_discord(event: NotificationEvent) -> bool:
    # / route to channel-specific webhook, fall back to default
    env_var = DISCORD_CHANNELS.get(event.channel, "DISCORD_WEBHOOK_SYSTEM")
    url = os.environ.get(env_var) or os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return False

    embed = {
        "title": f"{SEVERITY_EMOJI[event.severity]} {event.title}",
        "description": event.message,
        "color": SEVERITY_COLORS[event.severity],
    }
    if event.fields:
        embed["fields"] = [
            {"name": k, "value": str(v), "inline": True}
            for k, v in event.fields.items()
        ]

    try:
        await api_post(url, json={"embeds": [embed]}, timeout=5.0)
        return True
    except Exception as exc:
        logger.warning("discord_send_failed", error=str(exc))
        return False


async def _send_slack(event: NotificationEvent) -> bool:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return False

    fields_text = ""
    if event.fields:
        fields_text = "\n".join(f"*{k}:* {v}" for k, v in event.fields.items())

    text = f"{SEVERITY_EMOJI[event.severity]} *{event.title}*\n{event.message}"
    if fields_text:
        text += f"\n{fields_text}"

    try:
        await api_post(url, json={"text": text}, timeout=5.0)
        return True
    except Exception as exc:
        logger.warning("slack_send_failed", error=str(exc))
        return False


async def _send_telegram(event: NotificationEvent) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False

    fields_text = ""
    if event.fields:
        fields_text = "\n".join(f"<b>{k}:</b> {v}" for k, v in event.fields.items())

    text = f"{SEVERITY_EMOJI[event.severity]} <b>{event.title}</b>\n{event.message}"
    if fields_text:
        text += f"\n{fields_text}"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        await api_post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=5.0,
        )
        return True
    except Exception as exc:
        logger.warning("telegram_send_failed", error=str(exc))
        return False


async def _dispatch(event: NotificationEvent) -> dict[str, bool]:
    # / send to all configured channels
    results = {}
    for name, sender in [("discord", _send_discord), ("slack", _send_slack), ("telegram", _send_telegram)]:
        results[name] = await sender(event)
    return results


async def notify(event: NotificationEvent) -> None:
    # / fire-and-forget notification with throttle
    if _is_throttled(event):
        logger.debug("notification_throttled", title=event.title)
        return

    try:
        results = await _dispatch(event)
        sent = [ch for ch, ok in results.items() if ok]
        if sent:
            logger.info("notification_sent", title=event.title, channels=sent)
    except Exception as exc:
        logger.warning("notification_failed", title=event.title, error=str(exc))


def notify_async(event: NotificationEvent) -> asyncio.Task | None:
    # / fire-and-forget wrapper — returns task for testing, never awaited in prod
    try:
        loop = asyncio.get_running_loop()
        return loop.create_task(notify(event))
    except RuntimeError:
        logger.warning("no_event_loop_for_notification")
        return None


async def _flush_error_buffer() -> None:
    # / batch-send buffered errors every 60s
    global _error_buffer
    while True:
        await asyncio.sleep(60)
        if not _error_buffer:
            continue
        batch = _error_buffer.copy()
        _error_buffer.clear()
        for error_type, messages in batch.items():
            count = len(messages)
            sample = messages[0] if messages else ""
            event = NotificationEvent(
                severity=Severity.CRITICAL,
                title=f"{error_type} ({count}x in last 60s)",
                message=sample if count == 1 else f"{sample}\n... and {count - 1} more",
            )
            await notify(event)


def buffer_error(error_type: str, message: str) -> None:
    # / add error to batch buffer instead of sending immediately
    global _error_flush_task
    if error_type not in _error_buffer:
        _error_buffer[error_type] = []
    _error_buffer[error_type].append(message)

    # / start flush loop if not running
    if _error_flush_task is None or _error_flush_task.done():
        try:
            loop = asyncio.get_running_loop()
            _error_flush_task = loop.create_task(_flush_error_buffer())
        except RuntimeError:
            pass


# / convenience helpers for integration points

def notify_trade_executed(
    symbol: str, side: str, qty: float, price: float,
    strategy_id: str | None = None,
) -> asyncio.Task | None:
    return notify_async(NotificationEvent(
        severity=Severity.HIGH,
        title="trade executed",
        message=f"{side.upper()} {qty} {symbol} @ ${price:.2f}",
        fields={"strategy": strategy_id or "unknown"},
        channel="trades",
    ))


def notify_trade_error(
    symbol: str, side: str, error: str,
) -> asyncio.Task | None:
    buffer_error("trade_error", f"{side} {symbol}: {error}")
    return None


def notify_system_error(error: str, context: str = "") -> asyncio.Task | None:
    buffer_error("system_error", f"{context}: {error}" if context else error)
    return None


def notify_evolution_summary(summary: dict[str, Any]) -> asyncio.Task | None:
    gen = summary.get("generation", "?")
    killed = len(summary.get("killed", []))
    mutated = len(summary.get("mutated", []))
    promoted = len(summary.get("promoted", []))
    errors = len(summary.get("errors", []))
    return notify_async(NotificationEvent(
        severity=Severity.MEDIUM,
        title=f"evolution gen {gen} complete",
        message=f"killed {killed}, mutated {mutated}, promoted {promoted}",
        fields={"errors": str(errors)} if errors else {},
        channel="strategy",
    ))


def notify_strategy_promoted(strategy_id: str, sharpe: float, days: int) -> asyncio.Task | None:
    return notify_async(NotificationEvent(
        severity=Severity.HIGH,
        title="strategy promoted to live",
        message=f"{strategy_id} promoted after {days}d paper trading",
        fields={"sharpe": f"{sharpe:.2f}", "paper_days": str(days)},
        channel="strategy",
    ))


def notify_daily_digest(
    portfolio_value: float, daily_pnl: float, active_strategies: int,
    open_positions: int,
) -> asyncio.Task | None:
    pnl_pct = (daily_pnl / (portfolio_value - daily_pnl) * 100) if portfolio_value != daily_pnl else 0
    sign = "+" if daily_pnl >= 0 else ""
    return notify_async(NotificationEvent(
        severity=Severity.LOW,
        title="daily digest",
        message=f"portfolio: ${portfolio_value:,.2f} ({sign}{pnl_pct:.2f}%)",
        fields={
            "daily P&L": f"${sign}{daily_pnl:,.2f}",
            "strategies": str(active_strategies),
            "positions": str(open_positions),
        },
        channel="daily",
    ))


def notify_analysis_highlight(
    symbol: str, consensus: str, score: float,
) -> asyncio.Task | None:
    color = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡", "disagree": "🔵"}
    return notify_async(NotificationEvent(
        severity=Severity.MEDIUM,
        title=f"{color.get(consensus, '⚪')} {symbol} — {consensus}",
        message=f"composite score: {score:.1f}",
        channel="analysis",
    ))


def notify_sentiment_shift(
    symbol: str, old_score: float, new_score: float,
) -> asyncio.Task | None:
    delta = new_score - old_score
    direction = "bullish shift" if delta > 0 else "bearish shift"
    return notify_async(NotificationEvent(
        severity=Severity.MEDIUM,
        title=f"{symbol} — {direction}",
        message=f"sentiment {old_score:.2f} → {new_score:.2f} (Δ{delta:+.2f})",
        channel="sentiment",
    ))


def notify_daily_synthesis(synthesis: dict[str, Any]) -> asyncio.Task | None:
    buys = synthesis.get("top_buys", [])
    avoids = synthesis.get("top_avoids", [])
    risk = synthesis.get("portfolio_risk", "unknown")
    buy_text = ", ".join(b.get("symbol", "?") for b in buys[:5]) if buys else "none"
    avoid_text = ", ".join(a.get("symbol", "?") for a in avoids[:5]) if avoids else "none"
    return notify_async(NotificationEvent(
        severity=Severity.HIGH,
        title="daily synthesis (5PM ET)",
        message=f"buys: {buy_text}\navoids: {avoid_text}\nrisk: {risk}",
        channel="daily",
    ))
