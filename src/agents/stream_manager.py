# / owns alpaca + coinbase tick streams, broadcast fan-out, aggregator cycle

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import structlog

from src.agents import tools
from src.data.loop_registry import upsert_service_state
from src.data.market_data import store_latest_prices
from src.data.streams.alpaca_ws import AlpacaStream
from src.data.streams.base import TickBuffer
from src.data.streams.coinbase_ws import CoinbaseStream
from src.data.symbols import is_crypto

logger = structlog.get_logger(__name__)

# / per-symbol broadcast rate limit (mirrors orchestrator's PRICE_TICK_BROADCAST_MIN_INTERVAL)
_BROADCAST_MIN_INTERVAL_S = 1.0
_STREAM_FRESH_TICK_S = 90.0


class StreamManager:
    # / lifecycle for alpaca/coinbase websockets + tick aggregator + ws fan-out

    def __init__(self, broadcast_semaphore_size: int = 50) -> None:
        self.tick_buffer: TickBuffer | None = None
        self.alpaca_stream: AlpacaStream | None = None
        self.coinbase_stream: CoinbaseStream | None = None
        self.streamed_equity_symbols: set[str] = set()
        self._last_tick_broadcast: dict[str, float] = {}
        self._broadcast_semaphore = asyncio.Semaphore(broadcast_semaphore_size)
        self._broadcast_dropped = 0
        self._broadcast_drop_log_next = 0.0
        self._consecutive_failures: int = 0

    async def start(self, symbols: list[str]) -> None:
        # / split universe → equity (alpaca) + crypto (coinbase); start both
        equity_all = [s for s in symbols if not is_crypto(s)]
        crypto_syms = [s for s in symbols if is_crypto(s)]
        try:
            stream_cap = int(os.environ.get("ALPACA_STREAM_MAX_SYMBOLS", "30"))
        except ValueError:
            stream_cap = 30
        equity_streamed = equity_all[:stream_cap]
        equity_overflow = equity_all[stream_cap:]
        self.streamed_equity_symbols = set(equity_streamed)
        if equity_overflow:
            logger.info(
                "alpaca_stream_symbol_cap",
                cap=stream_cap, streamed=len(equity_streamed),
                overflow=len(equity_overflow),
                overflow_symbols=equity_overflow,
                note="overflow symbols use 5-min rest poll fallback",
            )

        self.tick_buffer = TickBuffer(max_per_symbol=1000)

        if equity_streamed:
            self.alpaca_stream = AlpacaStream(
                equity_streamed, self.tick_buffer, on_tick=self._on_tick_broadcast,
            )
            try:
                await self.alpaca_stream.start()
            except Exception as exc:
                logger.warning("alpaca_stream_start_failed", error=str(exc)[:200])
        if crypto_syms:
            self.coinbase_stream = CoinbaseStream(
                crypto_syms, self.tick_buffer, on_tick=self._on_tick_broadcast,
            )
            try:
                await self.coinbase_stream.start()
            except Exception as exc:
                logger.warning("coinbase_stream_start_failed", error=str(exc)[:200])

    async def stop(self) -> None:
        # / quench both streams; safe to call when not started
        for stream in (self.alpaca_stream, self.coinbase_stream):
            if stream is not None:
                try:
                    await stream.stop()
                except Exception as exc:
                    logger.debug("stream_stop_failed", error=str(exc)[:120])

    def _on_tick_broadcast(self, tick) -> None:
        # / rate-limited per-symbol fan-out to dashboard ws clients
        now = time.monotonic()
        last = self._last_tick_broadcast.get(tick.symbol, 0.0)
        if (now - last) < _BROADCAST_MIN_INTERVAL_S:
            return
        self._last_tick_broadcast[tick.symbol] = now
        try:
            from src.dashboard.app import _ws_clients  # / cycle: dashboard imports streams
            if not _ws_clients:
                return
            if self._broadcast_semaphore.locked():
                self._broadcast_dropped += 1
                if now >= self._broadcast_drop_log_next:
                    logger.warning(
                        "price_tick_broadcast_dropped",
                        dropped_total=self._broadcast_dropped,
                    )
                    self._broadcast_drop_log_next = now + 60.0
                return
            payload = {
                "symbol": tick.symbol,
                "price": tick.price,
                "timestamp_ms": tick.timestamp_ms,
                "vendor": tick.vendor,
            }
            tools.fire_and_forget(self._bounded_broadcast("price_tick", payload))
        except Exception:
            # / dashboard not mounted in this process
            pass

    async def _bounded_broadcast(self, event_type: str, payload: dict) -> None:
        async with self._broadcast_semaphore:
            try:
                from src.dashboard.app import broadcast  # / cycle: dashboard imports streams
                await broadcast(event_type, payload)
            except Exception as exc:
                logger.debug(
                    "bounded_broadcast_failed",
                    event=event_type, error=str(exc)[:120],
                )

    @staticmethod
    def _stream_healthy(s: Any) -> bool:
        if s is None or s.state.circuit_breaker.is_open or s.state.last_tick_at is None:
            return False
        return (time.monotonic() - s.state.last_tick_at) < _STREAM_FRESH_TICK_S

    def is_equity_healthy(self) -> bool:
        return self._stream_healthy(self.alpaca_stream)

    def is_crypto_healthy(self) -> bool:
        return self._stream_healthy(self.coinbase_stream)

    async def aggregate_once(self, pool) -> None:
        # / one aggregator cycle: drain buffer → upsert latest_prices
        if self.tick_buffer is None:
            return
        try:
            drained = await self.tick_buffer.drain_all()
            if drained:
                latest = {
                    sym: float(ticks[-1].price)
                    for sym, ticks in drained.items()
                    if ticks
                }
                if latest and pool is not None:
                    await store_latest_prices(pool, latest)
                    logger.debug("stream_aggregator_upserted", n=len(latest))
            if self._consecutive_failures > 0:
                self._consecutive_failures = 0
                await upsert_service_state(pool, "stream_aggregator", "ok", error=None)
        except Exception as exc:
            self._consecutive_failures += 1
            logger.warning(
                "stream_aggregator_error",
                error=str(exc)[:200],
                consecutive_failures=self._consecutive_failures,
            )
            if self._consecutive_failures >= 3:
                await upsert_service_state(
                    pool, "stream_aggregator", "error",
                    error=f"aggregator stuck ({self._consecutive_failures} consecutive failures): {str(exc)[:200]}",
                )
