# / push event to all connected websocket clients
# / consumed by orchestrator/executor/risk_agent via the dashboard package

from __future__ import annotations

import json

from src.dashboard.helpers.serializers import serialize_one
from src.dashboard.state import STATE


async def broadcast(event_type: str, data: dict) -> None:
    message = json.dumps({"type": event_type, "data": serialize_one(data)})
    disconnected = set()
    for ws in STATE.ws_clients:
        try:
            await ws.send_text(message)
        except (ConnectionError, RuntimeError, OSError):
            disconnected.add(ws)
    STATE.ws_clients.difference_update(disconnected)
