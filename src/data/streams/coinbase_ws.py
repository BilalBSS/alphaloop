# / coinbase advanced-trade-ws public ticker stream (no auth required)
# /
# / protocol: wss://advanced-trade-ws.coinbase.com
# /   client sends {type:"subscribe", product_ids:[...], channel:"ticker"}
# /   server streams messages of shape:
# /     {channel:"ticker", timestamp:"...", events:[{type:"update", tickers:[{
# /         product_id:"BTC-USD", price:"70123.45", volume_24_h:"...", best_bid, best_ask, ...
# /     }]}]}
# /
# / product ids match internal symbol format exactly (BTC-USD, ETH-USD, ...),
# / no translation needed.

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

import structlog
import websockets
from websockets.exceptions import ConnectionClosed

from src.data.streams.base import StreamBase, Tick

logger = structlog.get_logger(__name__)


COINBASE_WS_URL = os.getenv(
    "COINBASE_STREAM_URL", "wss://advanced-trade-ws.coinbase.com"
)


def _parse_ts(s: str | None) -> int:
    if not s:
        return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    try:
        if "." in s and len(s) > 26:
            head, tail = s.split(".", 1)
            tz_idx = max(tail.find("Z"), tail.find("+"), tail.find("-"))
            if tz_idx == -1:
                frac, tz_suffix = tail, ""
            else:
                frac, tz_suffix = tail[:tz_idx], tail[tz_idx:]
            frac = frac[:6]
            s = f"{head}.{frac}{tz_suffix}"
        s = s.replace("Z", "+00:00")
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except Exception:
        return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


class CoinbaseStream(StreamBase):
    # / public ticker channel — no auth, crypto-only universe.
    # / coinbase rate-throttles so we batch products into a single subscribe.

    @property
    def name(self) -> str:
        return "coinbase"

    async def _connect_and_consume(self) -> None:
        if not self.symbols:
            logger.info("coinbase_ws_no_symbols")
            await asyncio.sleep(30.0)
            return

        logger.info("coinbase_ws_connecting", url=COINBASE_WS_URL,
                    symbols=len(self.symbols))
        async with websockets.connect(COINBASE_WS_URL, ping_interval=20,
                                      ping_timeout=20, max_size=2**20) as ws:
            # / subscribe to ticker on all products in one message
            await ws.send(json.dumps({
                "type": "subscribe",
                "product_ids": self.symbols,
                "channel": "ticker",
            }))
            self._mark_connected()

            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
                except asyncio.TimeoutError:
                    continue
                except ConnectionClosed:
                    logger.info("coinbase_ws_closed")
                    return

                try:
                    msg = json.loads(raw)
                except Exception as exc:
                    logger.debug("coinbase_ws_decode_failed", error=str(exc)[:120])
                    continue

                await self._handle_message(msg)

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        # / only process ticker channel messages
        channel = msg.get("channel")
        if channel != "ticker":
            # / subscriptions/heartbeats/errors — ignore silently after setup
            if channel == "error" or msg.get("type") == "error":
                logger.warning("coinbase_ws_error_frame", msg=str(msg)[:200])
            return

        ts_msg = _parse_ts(msg.get("timestamp"))
        events = msg.get("events") or []
        for ev in events:
            tickers = ev.get("tickers") or []
            for t in tickers:
                sym = t.get("product_id")
                price = t.get("price")
                volume = t.get("volume_24_h")
                if sym is None or price is None:
                    continue
                try:
                    price_f = float(price)
                except (TypeError, ValueError):
                    continue
                try:
                    vol_f = float(volume) if volume is not None else None
                except (TypeError, ValueError):
                    vol_f = None
                await self._emit(Tick(
                    symbol=sym,
                    price=price_f,
                    volume=vol_f,
                    timestamp_ms=ts_msg,
                    vendor=self.name,
                ))
