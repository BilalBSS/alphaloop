from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.dashboard.state import STATE

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    origin = ws.headers.get("origin")
    if origin and origin not in STATE.cors_origins:
        await ws.close(code=1008, reason="origin not allowed")
        return

    await ws.accept()
    STATE.ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        STATE.ws_clients.discard(ws)
