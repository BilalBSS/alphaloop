# / alpaca iex stream — paper/free tier stocks websocket
# /
# / protocol: wss://stream.data.alpaca.markets/v2/iex
# /   1. connect
# /   2. server sends [{T:"success", msg:"connected"}]
# /   3. client sends {action:"auth", key, secret}
# /   4. server sends [{T:"success", msg:"authenticated"}]
# /   5. client sends {action:"subscribe", trades:[...], quotes:[...]}
# /   6. server streams arrays of event objects:
# /      - trade  {T:"t", S:"AAPL", p:182.34, s:100, t:"2026-04-20T13:30:00.123456789Z"}
# /      - quote  {T:"q", S:"AAPL", bp:182.33, ap:182.35, bs:1, as:2, t:"..."}
# /      - subscription/success/error frames
# /
# / reconnect + watchdog live in StreamBase; this module handles protocol only.

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


ALPACA_WS_URL = os.getenv(
    "ALPACA_STREAM_URL", "wss://stream.data.alpaca.markets/v2/iex"
)


def _parse_ts(s: str | None) -> int:
    # / alpaca ts is rfc3339 nanosecond precision; python datetime handles to micros.
    # / fallback: now() — better than dropping the tick.
    if not s:
        return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    try:
        # / trim to microseconds if nanosecond suffix is present
        if "." in s and len(s) > 26:
            head, tail = s.split(".", 1)
            # / keep up to 6 digits of fractional seconds
            tz_idx = max(tail.find("Z"), tail.find("+"), tail.find("-"))
            if tz_idx == -1:
                frac, tz_suffix = tail, ""
            else:
                frac, tz_suffix = tail[:tz_idx], tail[tz_idx:]
            frac = frac[:6]
            s = f"{head}.{frac}{tz_suffix}"
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return int(dt.timestamp() * 1000)
    except Exception:
        return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


class AlpacaStream(StreamBase):
    # / subscribes to trades (primary price) + quotes (fallback when no trade
    # / activity). each frame becomes a Tick via _emit. subclass contract: return
    # / cleanly from _connect_and_consume on disconnect so base class reconnects.

    @property
    def name(self) -> str:
        return "alpaca"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._api_key = os.getenv("ALPACA_API_KEY", "")
        self._api_secret = os.getenv("ALPACA_SECRET_KEY", "")

    async def _connect_and_consume(self) -> None:
        if not self._api_key or not self._api_secret:
            logger.warning("alpaca_ws_no_credentials")
            # / sleep a little so base-class doesn't hot-loop when creds missing
            await asyncio.sleep(30.0)
            return
        if not self.symbols:
            logger.info("alpaca_ws_no_symbols")
            await asyncio.sleep(30.0)
            return

        logger.info("alpaca_ws_connecting", url=ALPACA_WS_URL,
                    symbols=len(self.symbols))
        async with websockets.connect(ALPACA_WS_URL, ping_interval=20,
                                      ping_timeout=20, max_size=2**20) as ws:
            # / 1. server hello
            hello = await asyncio.wait_for(ws.recv(), timeout=10.0)
            hello_msg = json.loads(hello)
            if not self._is_connected_ack(hello_msg):
                logger.warning("alpaca_ws_unexpected_hello", msg=str(hello_msg)[:200])

            # / 2. auth
            await ws.send(json.dumps({
                "action": "auth",
                "key": self._api_key,
                "secret": self._api_secret,
            }))
            auth_resp = await asyncio.wait_for(ws.recv(), timeout=10.0)
            auth_msg = json.loads(auth_resp)
            if not self._is_auth_ack(auth_msg):
                raise RuntimeError(f"alpaca_auth_failed: {str(auth_msg)[:200]}")

            # / 3. subscribe
            await ws.send(json.dumps({
                "action": "subscribe",
                "trades": self.symbols,
                "quotes": self.symbols,
            }))
            sub_resp = await asyncio.wait_for(ws.recv(), timeout=10.0)
            logger.info("alpaca_ws_subscribed", response=str(sub_resp)[:200])

            # / 4. mark healthy and enter read loop
            self._mark_connected()

            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
                except asyncio.TimeoutError:
                    # / idle 60s with no frames — let watchdog / run-loop decide
                    continue
                except ConnectionClosed:
                    logger.info("alpaca_ws_closed")
                    return

                try:
                    frames = json.loads(raw)
                except Exception as exc:
                    logger.debug("alpaca_ws_decode_failed", error=str(exc)[:120])
                    continue

                if not isinstance(frames, list):
                    frames = [frames]
                for f in frames:
                    await self._handle_frame(f)

    async def _handle_frame(self, f: dict[str, Any]) -> None:
        t = f.get("T")
        if t == "t":  # / trade
            sym = f.get("S")
            price = f.get("p")
            size = f.get("s")
            ts = _parse_ts(f.get("t"))
            if sym is None or price is None:
                return
            await self._emit(Tick(
                symbol=sym,
                price=float(price),
                volume=float(size) if size is not None else None,
                timestamp_ms=ts,
                vendor=self.name,
            ))
        elif t == "q":  # / quote — mid-price as tick when no trade activity
            sym = f.get("S")
            bp = f.get("bp")
            ap = f.get("ap")
            if sym is None or bp is None or ap is None:
                return
            try:
                mid = (float(bp) + float(ap)) / 2.0
            except (TypeError, ValueError):
                return
            ts = _parse_ts(f.get("t"))
            await self._emit(Tick(
                symbol=sym,
                price=mid,
                volume=None,
                timestamp_ms=ts,
                vendor=self.name,
            ))
        elif t == "error":
            logger.warning("alpaca_ws_error_frame", frame=str(f)[:200])
        # / subscription/success frames are ignored after setup

    @staticmethod
    def _is_connected_ack(msg: Any) -> bool:
        if isinstance(msg, list):
            return any(
                isinstance(m, dict) and m.get("T") == "success"
                and m.get("msg") == "connected"
                for m in msg
            )
        return (isinstance(msg, dict) and msg.get("T") == "success"
                and msg.get("msg") == "connected")

    @staticmethod
    def _is_auth_ack(msg: Any) -> bool:
        if isinstance(msg, list):
            return any(
                isinstance(m, dict) and m.get("T") == "success"
                and m.get("msg") == "authenticated"
                for m in msg
            )
        return (isinstance(msg, dict) and msg.get("T") == "success"
                and msg.get("msg") == "authenticated")
