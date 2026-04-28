# / dashboard runtime state — replaces scattered module-level globals

from __future__ import annotations

import os
import time
from collections import OrderedDict
from typing import Any

import asyncpg
import structlog
from fastapi import WebSocket

logger = structlog.get_logger(__name__)


class TTLCache:
    # / lru-ish cache with monotonic ttl

    def __init__(self, max_entries: int, ttl_s: float, clock=time.monotonic) -> None:
        self._max = max_entries
        self._ttl = ttl_s
        self._clock = clock
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key: Any) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, payload = entry
        if self._clock() >= expires_at:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return payload

    def put(self, key: Any, payload: Any) -> None:
        if key not in self._data and len(self._data) >= self._max:
            # / evict the entry closest to expiry
            oldest = min(self._data, key=lambda k: self._data[k][0])
            self._data.pop(oldest, None)
        self._data[key] = (self._clock() + self._ttl, payload)
        self._data.move_to_end(key)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


class DashboardState:
    # / single bag of dashboard runtime state

    def __init__(self) -> None:
        self.pool: asyncpg.Pool | None = None
        self.ws_clients: set[WebSocket] = set()
        self.broker: Any = None
        self.feature_bench_cache: dict = {}
        self.intraday_cache = TTLCache(max_entries=256, ttl_s=30.0)
        self.tail_dep_cache = TTLCache(max_entries=16, ttl_s=300.0, clock=time.time)
        self.admin_token: str = ""
        self.cors_origins: list[str] = []
        self.prod_mode: bool = False

    def load_config_from_env(self) -> None:
        # / called once at boot
        default_origins = [
            "https://dashboard.siddiqtradebot.trade",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
        cors_env = os.environ.get("ALPHALOOP_CORS_ORIGINS", "").strip()
        parsed = [o.strip() for o in cors_env.split(",") if o.strip()] if cors_env else []
        self.cors_origins = parsed or default_origins

        self.prod_mode = os.environ.get("PROD", "").strip().lower() in ("1", "true", "yes")
        if (self.prod_mode and not cors_env
                and any("localhost" in o or "127.0.0.1" in o for o in self.cors_origins)):
            raise RuntimeError(
                "PROD=1 but ALPHALOOP_CORS_ORIGINS unset and resolved origins still "
                "include localhost. set ALPHALOOP_CORS_ORIGINS=<prod hostname(s)>."
            )

        self.admin_token = os.environ.get("ADMIN_TOKEN", "").strip()
        if self.admin_token and len(self.admin_token) < 32:
            raise RuntimeError(
                "ADMIN_TOKEN is set but shorter than 32 chars — refuse to boot. "
                "generate via `openssl rand -hex 32` or similar."
            )
        if not self.admin_token:
            logger.warning(
                "admin_token_unset",
                hint="ADMIN_TOKEN env unset — /ws and mutating dashboard endpoints are unauth'd",
            )

    def get_broker(self) -> Any:
        # / lazy alpaca init
        if self.broker is None:
            from src.brokers.alpaca import AlpacaBroker
            self.broker = AlpacaBroker()
        return self.broker

    async def aclose(self) -> None:
        # / shutdown teardown
        self.intraday_cache.clear()
        self.tail_dep_cache.clear()
        self.feature_bench_cache.clear()
        self.ws_clients.clear()
        self.pool = None


# / module-level singleton; lifespan populates pool, env loads config
STATE = DashboardState()


def get_state() -> DashboardState:
    # / fastapi dependency provider
    return STATE
