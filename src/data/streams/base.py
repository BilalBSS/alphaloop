# / shared primitives for websocket streams: buffer, circuit breaker, lifecycle.
# /
# / any vendor-specific stream module subclasses StreamBase and implements:
# /   - _connect_and_consume(): coroutine that opens the ws and emits ticks
# /   - name: short identifier for logs/metrics (e.g. "alpaca", "coinbase")
# /
# / the base class wraps that coroutine with:
# /   - exponential backoff reconnect (1s → 2s → 4s → ... cap 60s)
# /   - circuit breaker (3 disconnects within 5 min → open → fallback)
# /   - tick watchdog (force reconnect if no tick received for N seconds)
# /   - graceful shutdown on .stop()
# /   - bounded in-memory tick buffer per symbol (drop-oldest past max_size)

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Any, Callable, Deque, Literal

import structlog

try:
    from zoneinfo import ZoneInfo
    _NY_TZ = ZoneInfo("America/New_York")
except Exception:
    _NY_TZ = None

logger = structlog.get_logger(__name__)


def _is_lunch_hour_et(now: datetime | None = None) -> bool:
    # / 12:00-13:00 ET — low-liquidity names can genuinely go a minute or two
    # / between prints. the aggressive 30s watchdog false-alarms here.
    if _NY_TZ is None:
        return False
    now = now or datetime.now(_NY_TZ)
    et = now.astimezone(_NY_TZ)
    return dtime(12, 0) <= et.time() < dtime(13, 0)


StreamStatus = Literal["connecting", "connected", "reconnecting", "circuit_open", "stopped"]


@dataclass
class Tick:
    symbol: str
    price: float
    volume: float | None  # / None when the frame didn't include a volume (quote-only)
    timestamp_ms: int
    vendor: str


class TickBuffer:
    # / bounded per-symbol ring of the most-recent ticks. drop-oldest on overflow.
    # / read-only snapshots are cheap — the deque holds references, not copies.
    # / dropped_ticks tracks overflow count; _log_suppress_until rate-limits the
    # / overflow warning so a single hot symbol can't spam the log.

    __slots__ = (
        "_by_symbol", "_max_per_symbol", "_lock",
        "_dropped", "_log_suppress_until",
    )

    def __init__(self, max_per_symbol: int = 1000) -> None:
        self._by_symbol: dict[str, Deque[Tick]] = {}
        self._max_per_symbol = max_per_symbol
        self._lock = asyncio.Lock()
        self._dropped: dict[str, int] = {}
        self._log_suppress_until: dict[str, float] = {}

    async def push(self, tick: Tick) -> None:
        async with self._lock:
            buf = self._by_symbol.get(tick.symbol)
            if buf is None:
                buf = deque(maxlen=self._max_per_symbol)
                self._by_symbol[tick.symbol] = buf
            # / at maxlen, append silently drops the oldest; count + log it
            # / so we can correlate pricing-staleness incidents to stream pressure.
            if len(buf) >= self._max_per_symbol:
                self._dropped[tick.symbol] = self._dropped.get(tick.symbol, 0) + 1
                now = time.monotonic()
                next_ok = self._log_suppress_until.get(tick.symbol, 0.0)
                if now >= next_ok:
                    logger.warning(
                        "stream_buffer_overflow",
                        symbol=tick.symbol, vendor=tick.vendor,
                        dropped_total=self._dropped[tick.symbol],
                        max_per_symbol=self._max_per_symbol,
                    )
                    self._log_suppress_until[tick.symbol] = now + 60.0
            buf.append(tick)

    def dropped_ticks(self) -> dict[str, int]:
        # / snapshot for /api/phase5-metrics. no lock — reads atomic in CPython.
        return dict(self._dropped)

    async def drain(self, symbol: str) -> list[Tick]:
        # / grab-and-clear for aggregation passes
        async with self._lock:
            buf = self._by_symbol.get(symbol)
            if not buf:
                return []
            out = list(buf)
            buf.clear()
            return out

    async def drain_all(self) -> dict[str, list[Tick]]:
        async with self._lock:
            out = {s: list(b) for s, b in self._by_symbol.items() if b}
            for b in self._by_symbol.values():
                b.clear()
            return out

    async def latest(self, symbol: str) -> Tick | None:
        async with self._lock:
            buf = self._by_symbol.get(symbol)
            return buf[-1] if buf else None


@dataclass
class CircuitBreaker:
    # / opens after `threshold` disconnects inside `window_s` seconds; reset after
    # / `reset_s` of stability. caller reads `.is_open` to decide whether to keep
    # / trying or fall back to REST polling.
    threshold: int = 3
    window_s: float = 300.0
    reset_s: float = 120.0
    _events: list[float] = field(default_factory=list)
    _open_until: float = 0.0

    def record_disconnect(self) -> None:
        now = time.monotonic()
        self._events = [t for t in self._events if (now - t) < self.window_s]
        self._events.append(now)
        if len(self._events) >= self.threshold:
            self._open_until = now + self.reset_s

    def record_healthy(self) -> None:
        now = time.monotonic()
        self._events = [t for t in self._events if (now - t) < self.window_s]
        # / require continuous healthy window to clear the gate
        if not self._events and now >= self._open_until:
            self._open_until = 0.0

    @property
    def is_open(self) -> bool:
        return time.monotonic() < self._open_until


@dataclass
class StreamState:
    name: str
    status: StreamStatus = "stopped"
    connected_at: float | None = None
    last_tick_at: float | None = None
    reconnect_attempts: int = 0
    consecutive_errors: int = 0
    total_ticks: int = 0
    last_error: str | None = None
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "connected_at": self.connected_at,
            "last_tick_age_s": (
                time.monotonic() - self.last_tick_at if self.last_tick_at else None
            ),
            "reconnect_attempts": self.reconnect_attempts,
            "consecutive_errors": self.consecutive_errors,
            "total_ticks": self.total_ticks,
            "last_error": self.last_error,
            "circuit_open": self.circuit_breaker.is_open,
        }


class StreamBase(ABC):
    # / every vendor stream extends this class. the run loop owns reconnect
    # / backoff + circuit breaker; subclasses just implement _connect_and_consume.

    MAX_BACKOFF_S = 60.0
    WATCHDOG_IDLE_S = 30.0
    WATCHDOG_IDLE_LUNCH_S = 120.0  # / lunch-hour ET window is genuinely quieter

    def __init__(
        self,
        symbols: list[str],
        buffer: TickBuffer,
        *,
        on_tick: Callable[[Tick], Any] | None = None,
    ) -> None:
        self.symbols = [s for s in symbols if s]
        self.buffer = buffer
        self.on_tick = on_tick
        self.state = StreamState(name=self.name)
        self._stop = asyncio.Event()
        self._run_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def _connect_and_consume(self) -> None:
        # / subclass: open websocket, subscribe to self.symbols, loop reading frames,
        # / call `await self._emit(Tick(...))` on each relevant frame, return cleanly
        # / on disconnect so the base-class loop can schedule a reconnect.
        ...

    async def _emit(self, tick: Tick) -> None:
        self.state.total_ticks += 1
        self.state.last_tick_at = time.monotonic()
        await self.buffer.push(tick)
        cb = self.on_tick
        if cb is not None:
            try:
                res = cb(tick)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                logger.warning("stream_on_tick_callback_failed",
                               stream=self.name, error=str(exc)[:160])

    async def start(self) -> None:
        if self._run_task and not self._run_task.done():
            return
        self._stop.clear()
        self.state.status = "connecting"
        self._run_task = asyncio.create_task(self._run_loop(), name=f"{self.name}_stream")
        self._watchdog_task = asyncio.create_task(self._watchdog(), name=f"{self.name}_watchdog")
        logger.info("stream_started", stream=self.name, symbols=len(self.symbols))

    async def stop(self) -> None:
        self._stop.set()
        self.state.status = "stopped"
        for task in (self._run_task, self._watchdog_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        logger.info("stream_stopped", stream=self.name,
                    ticks=self.state.total_ticks,
                    reconnects=self.state.reconnect_attempts)

    async def _run_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            if self.state.circuit_breaker.is_open:
                self.state.status = "circuit_open"
                logger.warning("stream_circuit_open_pausing", stream=self.name)
                # / wait full reset_s before re-attempting — caller (orchestrator)
                # / is running REST fallback during this window
                if await self._sleep_or_stop(60.0):
                    return
                continue

            try:
                self.state.status = "connecting" if self.state.reconnect_attempts == 0 else "reconnecting"
                await self._connect_and_consume()
                # / clean return = disconnected by peer. record and backoff.
                self.state.circuit_breaker.record_disconnect()
                self.state.reconnect_attempts += 1
                logger.info("stream_disconnected_clean", stream=self.name,
                            backoff_s=backoff)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.state.consecutive_errors += 1
                self.state.last_error = f"{exc.__class__.__name__}: {str(exc)[:180]}"
                self.state.circuit_breaker.record_disconnect()
                self.state.reconnect_attempts += 1
                logger.warning("stream_error_reconnecting",
                               stream=self.name, error=self.state.last_error,
                               backoff_s=backoff)

            if self._stop.is_set():
                return
            if await self._sleep_or_stop(backoff):
                return
            backoff = min(backoff * 2, self.MAX_BACKOFF_S)

    async def _watchdog(self) -> None:
        # / force reconnect if no tick received during market hours for a long while.
        # / threshold relaxes to WATCHDOG_IDLE_LUNCH_S during 12:00-13:00 ET so the
        # / low-liquidity lunch lull doesn't trip needless reconnects.
        while not self._stop.is_set():
            if await self._sleep_or_stop(5.0):
                return
            if self.state.last_tick_at is None:
                continue
            idle = time.monotonic() - self.state.last_tick_at
            threshold = (
                self.WATCHDOG_IDLE_LUNCH_S
                if _is_lunch_hour_et()
                else self.WATCHDOG_IDLE_S
            )
            if idle > threshold and self.state.status == "connected":
                logger.warning("stream_watchdog_idle_reconnect",
                               stream=self.name, idle_s=round(idle, 1),
                               threshold_s=threshold)
                # / setting status triggers the subclass consume loop to detect it
                # / on its next iteration (subclasses check self._stop + state)
                self.state.status = "reconnecting"
                # / cancel the current connect task — the run loop will reconnect
                if self._run_task and not self._run_task.done():
                    self._run_task.cancel()
                    try:
                        await self._run_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    self._run_task = asyncio.create_task(
                        self._run_loop(), name=f"{self.name}_stream",
                    )

    async def _sleep_or_stop(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    def _mark_connected(self) -> None:
        self.state.status = "connected"
        self.state.connected_at = time.monotonic()
        self.state.consecutive_errors = 0
        self.state.circuit_breaker.record_healthy()
