# / daily budget gate for gemini vision calls
# / reads api_costs (+ pending in-memory + in-flight reservations) to stay under GEMINI_DAILY_CAP

from __future__ import annotations

import asyncio
import os

import structlog

from src.data.cost_tracker import get_daily_call_count

logger = structlog.get_logger(__name__)

# / sources that count against the gemini daily cap (accept both aliases)
_GEMINI_SOURCES = ("gemini", "gemini-3-flash", "gemini-vision")
_DEFAULT_DAILY_CAP = 60

# / module-level in-flight counter — bridges the check-then-call window
# / a call reserves a slot BEFORE firing gemini; cost_tracker increments on success
# / release always fires in finally, so net accounting: success = +1 real -0 reserved,
# / failure = +0 real -0 reserved — cap is never breached by concurrent checks
_IN_FLIGHT: int = 0
_IN_FLIGHT_LOCK = asyncio.Lock()


async def reserve_call() -> None:
    # / add one pending call to the in-flight counter before starting the gemini request
    global _IN_FLIGHT
    async with _IN_FLIGHT_LOCK:
        _IN_FLIGHT += 1


async def release_call() -> None:
    # / remove one pending call after the gemini request returns (success or failure)
    global _IN_FLIGHT
    async with _IN_FLIGHT_LOCK:
        _IN_FLIGHT = max(0, _IN_FLIGHT - 1)


async def current_in_flight() -> int:
    # / read accessor for tests
    async with _IN_FLIGHT_LOCK:
        return _IN_FLIGHT


class VisionBudget:
    # / budget gate for gemini daily call cap; pre-debits in-flight calls to avoid races

    def __init__(self, pool, daily_cap: int | None = None):
        self._pool = pool
        try:
            env_cap = int(os.environ.get("GEMINI_DAILY_CAP", str(_DEFAULT_DAILY_CAP)))
        except ValueError:
            env_cap = _DEFAULT_DAILY_CAP
        self._daily_cap = int(daily_cap) if daily_cap is not None else env_cap

    @property
    def daily_cap(self) -> int:
        return self._daily_cap

    async def current_usage(self, pool=None) -> int:
        # / sum today's gemini call counts across all gemini source aliases + in-flight
        p = pool or self._pool
        total = 0
        for source in _GEMINI_SOURCES:
            try:
                total += await get_daily_call_count(p, source)
            except Exception as exc:
                logger.warning("budget_gate_read_failed", source=source, error=str(exc)[:120])
        total += await current_in_flight()
        return total

    async def can_call_gemini(self, pool=None) -> bool:
        # / true if we have room under the daily cap (incl. in-flight reservations)
        if self._daily_cap <= 0:
            return False
        used = await self.current_usage(pool or self._pool)
        if used >= self._daily_cap:
            logger.info("vision_cap_reached", used=used, cap=self._daily_cap)
            return False
        return True
