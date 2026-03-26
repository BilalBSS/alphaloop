# / broker factory: routes symbols to the correct broker
# / stocks -> alpaca, crypto -> alpaca (initially), paper -> paper broker
# / wraps real brokers with paper broker for simulated trading

from __future__ import annotations

import structlog

from src.data.symbols import is_crypto

from .alpaca_broker import AlpacaBroker
from .base import BrokerInterface
from .paper_broker import PaperBroker

logger = structlog.get_logger(__name__)


class BrokerFactory:
    def __init__(self, mode: str = "paper", initial_cash: float = 100_000.0):
        # / mode: "paper" for simulated, "live" for real orders
        self._mode = mode
        self._paper = PaperBroker(initial_cash=initial_cash) if mode == "paper" else None
        self._alpaca = AlpacaBroker() if mode == "live" else None

    def get_broker(self, symbol: str | None = None) -> BrokerInterface:
        # / returns the appropriate broker for the given symbol
        # / in paper mode, always returns paper broker
        # / in live mode, returns alpaca (stocks + crypto both go through alpaca)
        if self._mode == "paper":
            return self._paper  # type: ignore[return-value]
        return self._alpaca  # type: ignore[return-value]

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def paper_broker(self) -> PaperBroker | None:
        return self._paper


def create_broker(mode: str = "paper", initial_cash: float = 100_000.0) -> BrokerFactory:
    # / convenience function to create a broker factory
    logger.info("broker_factory_created", mode=mode, initial_cash=initial_cash)
    return BrokerFactory(mode=mode, initial_cash=initial_cash)
