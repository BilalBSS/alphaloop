
from __future__ import annotations

import time

import asyncpg
import structlog

from src.agents.market_tools import fetch_peak_equity, store_peak_equity

logger = structlog.get_logger(__name__)


class CircuitBreakerState:

    def __init__(self, max_drawdown: float, loss_pause_seconds: int) -> None:
        self._peak_equity: float = 0.0
        self._paused_until: float = 0.0
        self._last_pause_marker: float = 0.0
        self._max_drawdown = max_drawdown
        self._loss_pause_seconds = loss_pause_seconds
        self._initialized = False

    async def init_from_db(self, pool) -> None:
        # / one-shot restore from portfolio_snapshots
        if self._initialized:
            return
        restored = await fetch_peak_equity(pool)
        if restored > 0:
            self._peak_equity = restored
            logger.info("peak_equity_restored", peak=restored)
        self._initialized = True

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    def is_paused(self, now: float | None = None) -> bool:
        return (now or time.time()) < self._paused_until

    def arm_loss_pause(self, marker: float, now: float | None = None) -> bool:
        # / one pause per streak
        if marker <= self._last_pause_marker:
            return False
        self._paused_until = (now or time.time()) + self._loss_pause_seconds
        self._last_pause_marker = marker
        return True

    async def update_peak(self, pool, equity: float) -> float:
        if equity > self._peak_equity:
            self._peak_equity = equity
        try:
            await store_peak_equity(pool, equity, self._peak_equity)
        except (asyncpg.PostgresError, ConnectionError, OSError) as exc:
            logger.warning("store_peak_equity_failed", error=str(exc)[:80])
        return self._peak_equity

    def current_drawdown(self, equity: float) -> float:
        if self._peak_equity <= 0:
            return 0.0
        return (equity - self._peak_equity) / self._peak_equity

    def hard_stop_breached(self, equity: float) -> bool:
        return self.current_drawdown(equity) < self._max_drawdown

    def reset(self) -> None:
        # / explicit reset for tests
        self._peak_equity = 0.0
        self._paused_until = 0.0
        self._last_pause_marker = 0.0
        self._initialized = False
