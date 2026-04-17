# / orchestrator loops for chart vision + chart pruning
# / registered by the agent orchestrator; safe to run indefinitely

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import structlog

from src.vision.chart_analyzer import analyze_symbol_chart
from src.vision.chart_rotator import rotate_old_charts

logger = structlog.get_logger(__name__)

# / chart vision windows — 08:30 ET + 16:15 ET (fractional hours)
DEFAULT_WINDOWS_ET: tuple[float, ...] = (8.5, 16.25)
# / rotation runs daily at 03:00 ET
DEFAULT_ROTATE_HOUR_ET: int = 3
DEFAULT_RETENTION_DAYS: int = 30

# / small gap between per-symbol calls so we don't burst the rpm bucket
_PER_SYMBOL_DELAY_SECONDS = 1.5


def _et_tz():
    # / dst-aware eastern time, fallback to fixed est
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except Exception:
        return timezone(timedelta(hours=-5))


def _seconds_until_next_window_et(windows: tuple[float, ...]) -> float:
    # / compute wait (in seconds) until the next fractional-hour window in ET
    et = _et_tz()
    now = datetime.now(et)
    candidates: list[datetime] = []
    for w in windows:
        hour = int(w)
        minute = int(round((w - hour) * 60))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            continue
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate = candidate + timedelta(days=1)
        candidates.append(candidate)
    if not candidates:
        # / fallback — 1h sleep so the loop stays alive even if config is empty
        return 3600.0
    wait = (min(candidates) - now).total_seconds()
    return max(1.0, wait)


def _seconds_until_hour_et(hour: int) -> float:
    # / compute seconds until the next occurrence of `hour`:00 in ET
    et = _et_tz()
    now = datetime.now(et)
    target = now.replace(hour=int(hour), minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


async def chart_vision_loop(
    pool,
    targets_fn: Callable[[], Awaitable[list[str]]],
    windows: tuple[float, ...] = DEFAULT_WINDOWS_ET,
    timeframe: str = "1D",
) -> None:
    # / wake at each ET window, fetch target symbols via callback, analyze one-by-one
    logger.info("chart_vision_loop_starting", windows=list(windows), timeframe=timeframe)
    while True:
        try:
            wait = _seconds_until_next_window_et(windows)
            logger.info("chart_vision_loop_sleeping", seconds=round(wait, 1))
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("chart_vision_loop_sleep_error", error=str(exc)[:200])
            await asyncio.sleep(600)
            continue

        try:
            targets = await targets_fn()
        except Exception as exc:
            logger.error("chart_vision_targets_failed", error=str(exc)[:200])
            targets = []

        if not targets:
            logger.info("chart_vision_loop_no_targets")
            continue

        logger.info("chart_vision_loop_run", symbols=len(targets), timeframe=timeframe)
        succeeded = 0
        for i, symbol in enumerate(targets):
            try:
                result = await analyze_symbol_chart(pool, symbol, timeframe)
                if result is not None:
                    succeeded += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "chart_vision_symbol_failed",
                    symbol=symbol, error=str(exc)[:200],
                )
            if i < len(targets) - 1:
                await asyncio.sleep(_PER_SYMBOL_DELAY_SECONDS)
        logger.info(
            "chart_vision_loop_complete",
            succeeded=succeeded, total=len(targets), timeframe=timeframe,
        )


async def chart_rotation_loop(
    retention_days: int = DEFAULT_RETENTION_DAYS,
    rotate_hour_et: int = DEFAULT_ROTATE_HOUR_ET,
) -> None:
    # / daily at 03:00 ET, prune png files older than retention_days
    logger.info(
        "chart_rotation_loop_starting",
        retention_days=retention_days, hour_et=rotate_hour_et,
    )
    while True:
        try:
            wait = _seconds_until_hour_et(rotate_hour_et)
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("chart_rotation_loop_sleep_error", error=str(exc)[:200])
            await asyncio.sleep(3600)
            continue

        try:
            deleted = await rotate_old_charts(days=retention_days)
            logger.info("chart_rotation_loop_run", deleted=deleted)
        except Exception as exc:
            logger.error("chart_rotation_loop_error", error=str(exc)[:200])
