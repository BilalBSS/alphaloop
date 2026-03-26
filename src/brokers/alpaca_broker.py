# / alpaca broker: rest api for stocks + crypto
# / uses httpx for async requests, integrates with resilience module
# / supports market, limit, stop, stop_limit order types

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable

import httpx
import structlog

from src.data.resilience import with_retry
from src.data.symbols import to_alpaca, is_crypto

from .base import AccountBalance, BrokerInterface, Order, Position

logger = structlog.get_logger(__name__)

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"


def _headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
    }


def _base_url() -> str:
    return os.environ.get("ALPACA_BASE_URL", PAPER_URL)


def _parse_order(data: dict[str, Any]) -> Order:
    # / parse alpaca order response into our order dataclass
    filled_price = None
    if data.get("filled_avg_price"):
        filled_price = float(data["filled_avg_price"])

    return Order(
        order_id=data["id"],
        symbol=data["symbol"],
        side=data["side"],
        qty=float(data.get("qty") or data.get("notional") or 0),
        order_type=data["type"],
        status=data["status"],
        filled_qty=float(data.get("filled_qty", 0)),
        filled_price=filled_price,
        limit_price=float(data["limit_price"]) if data.get("limit_price") else None,
        stop_price=float(data["stop_price"]) if data.get("stop_price") else None,
        created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")) if data.get("created_at") else None,
        filled_at=datetime.fromisoformat(data["filled_at"].replace("Z", "+00:00")) if data.get("filled_at") else None,
    )


class AlpacaBroker(BrokerInterface):
    def __init__(self):
        self._base = _base_url()

    @with_retry(source="alpaca_broker", max_retries=2, base_delay=1.0)
    async def get_price(self, symbol: str) -> float:
        # / get latest trade price
        alpaca_sym = to_alpaca(symbol)
        crypto = is_crypto(symbol)

        if crypto:
            url = f"{DATA_URL}/v1beta3/crypto/us/latest/trades?symbols={alpaca_sym}"
        else:
            url = f"{DATA_URL}/v2/stocks/{alpaca_sym}/trades/latest"

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=_headers())
            resp.raise_for_status()
            data = resp.json()

        if crypto:
            trades = data.get("trades", {})
            trade = trades.get(alpaca_sym, {})
            return float(trade.get("p", 0))
        else:
            return float(data.get("trade", {}).get("p", 0))

    @with_retry(source="alpaca_broker", max_retries=2, base_delay=1.0)
    async def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> Order:
        alpaca_sym = to_alpaca(symbol)
        payload: dict[str, Any] = {
            "symbol": alpaca_sym,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": "day" if not is_crypto(symbol) else "gtc",
        }
        if limit_price is not None:
            payload["limit_price"] = str(limit_price)
        if stop_price is not None:
            payload["stop_price"] = str(stop_price)

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._base}/v2/orders",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        order = _parse_order(data)
        logger.info(
            "alpaca_order_placed",
            order_id=order.order_id, symbol=symbol,
            side=side, qty=qty, type=order_type,
        )
        return order

    @with_retry(source="alpaca_broker", max_retries=2, base_delay=1.0)
    async def get_positions(self) -> list[Position]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self._base}/v2/positions",
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        positions = []
        for p in data:
            positions.append(Position(
                symbol=p["symbol"],
                qty=float(p["qty"]),
                avg_entry_price=float(p["avg_entry_price"]),
                current_price=float(p["current_price"]),
                market_value=float(p["market_value"]),
                unrealized_pnl=float(p["unrealized_pl"]),
                side="long" if float(p["qty"]) > 0 else "short",
            ))
        return positions

    @with_retry(source="alpaca_broker", max_retries=2, base_delay=1.0)
    async def get_account_balance(self) -> AccountBalance:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self._base}/v2/account",
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        return AccountBalance(
            equity=float(data["equity"]),
            cash=float(data["cash"]),
            buying_power=float(data["buying_power"]),
            portfolio_value=float(data["portfolio_value"]),
            positions_value=float(data["equity"]) - float(data["cash"]),
        )

    @with_retry(source="alpaca_broker", max_retries=2, base_delay=1.0)
    async def cancel_order(self, order_id: str) -> bool:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                f"{self._base}/v2/orders/{order_id}",
                headers=_headers(),
            )
        return resp.status_code in (200, 204)

    @with_retry(source="alpaca_broker", max_retries=2, base_delay=1.0)
    async def get_order_status(self, order_id: str) -> Order:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self._base}/v2/orders/{order_id}",
                headers=_headers(),
            )
            resp.raise_for_status()
            return _parse_order(resp.json())

    async def stream_prices(
        self,
        symbols: list[str],
        callback: Callable[[str, float], Any],
    ) -> None:
        # / poll-based price streaming (websocket upgrade in future)
        # / fetches latest prices for all symbols and calls callback
        for symbol in symbols:
            try:
                price = await self.get_price(symbol)
                if price > 0:
                    await callback(symbol, price)
            except Exception as exc:
                logger.warning("stream_price_error", symbol=symbol, error=type(exc).__name__)
