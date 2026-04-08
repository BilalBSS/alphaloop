# / time-weighted average price execution — splits large orders into slices

from __future__ import annotations

import asyncio

import structlog

from src.brokers.base import BrokerInterface

logger = structlog.get_logger(__name__)


class TwapExecutor:
    def __init__(
        self,
        broker: BrokerInterface,
        num_slices: int = 5,
        interval_seconds: int = 60,
        min_order_value: float = 1000,
    ):
        self._broker = broker
        self._num_slices = num_slices
        self._interval = interval_seconds
        self._min_value = min_order_value

    async def execute(
        self, symbol: str, qty: float, side: str, price: float,
    ) -> list[dict]:
        # / split order into slices and execute over time
        order_value = qty * price
        if order_value < self._min_value:
            result = await self._broker.place_order(symbol, qty, side)
            return [result]

        slice_qty = qty / self._num_slices
        results = []
        for i in range(self._num_slices):
            try:
                result = await self._broker.place_order(symbol, slice_qty, side)
                results.append(result)
                logger.info("twap_slice_filled", symbol=symbol, slice=i + 1, qty=slice_qty)
            except Exception as exc:
                logger.error("twap_slice_failed", symbol=symbol, slice=i + 1, error=str(exc))
                break
            if i < self._num_slices - 1:
                await asyncio.sleep(self._interval)
        return results
