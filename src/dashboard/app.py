
from __future__ import annotations

import os
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.dashboard.helpers.broadcast import broadcast
from src.dashboard.helpers.db import query as _query
from src.dashboard.helpers.db import query_one as _query_one
from src.dashboard.helpers.serializers import serialize as _serialize
from src.dashboard.helpers.serializers import serialize_one as _serialize_one
from src.dashboard.helpers.serializers import serialize_position as _serialize_position
from src.dashboard.routers import (
    admin,
    alerts,
    charts,
    compare,
    drawings,
    evolution,
    health,
    macro,
    markers,
    portfolio,
    portfolio_risk,
    replay,
    symbol_data,
    system_metrics,
    volume_profile,
    websocket,
)
from src.dashboard.routers.charts import _intraday_cache_key
from src.dashboard.routers.portfolio import STRATEGY_CONFIGS_DIR
from src.dashboard.state import STATE
from src.data.db import close_db, init_db

logger = structlog.get_logger(__name__)

STATE.load_config_from_env()

_ws_clients = STATE.ws_clients


def _get_broker():
    # / lazy alpaca init
    return STATE.get_broker()


def _intraday_cache_get(key: tuple) -> object | None:
    return STATE.intraday_cache.get(key)


def _intraday_cache_put(key: tuple, payload: object) -> None:
    STATE.intraday_cache.put(key, payload)


def _intraday_cache_clear() -> None:
    STATE.intraday_cache.clear()


__all__ = [
    "STATE",
    "STRATEGY_CONFIGS_DIR",
    "_get_broker",
    "_intraday_cache_clear",
    "_intraday_cache_get",
    "_intraday_cache_key",
    "_intraday_cache_put",
    "_query",
    "_query_one",
    "_serialize",
    "_serialize_one",
    "_serialize_position",
    "_ws_clients",
    "app",
    "broadcast",
    "run",
]

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE.pool = await init_db()
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    yield
    await STATE.aclose()
    await close_db()


app = FastAPI(title="Quant Trading Dashboard", docs_url="/api/docs", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=STATE.cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _head_fallback(request, call_next):
    if request.method == "HEAD" and request.url.path.startswith("/api/"):
        request.scope["method"] = "GET"
    return await call_next(request)


@app.middleware("http")
async def _no_cache_html(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html") or path.endswith("/index.html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    logger.error(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        error_type=type(exc).__name__,
        traceback=traceback.format_exc(),
    )
    return JSONResponse(status_code=500, content={"detail": "internal error"})


app.include_router(portfolio.router)
app.include_router(symbol_data.router)
app.include_router(system_metrics.router)
app.include_router(macro.router)
app.include_router(health.router)
app.include_router(charts.router)
app.include_router(markers.router)
app.include_router(drawings.router)
app.include_router(alerts.router)
app.include_router(replay.router)
app.include_router(compare.router)
app.include_router(volume_profile.router)
app.include_router(evolution.router)
app.include_router(portfolio_risk.router)
app.include_router(admin.router)
app.include_router(websocket.router)


def run():
    import uvicorn
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
