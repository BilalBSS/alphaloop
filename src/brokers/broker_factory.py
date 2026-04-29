
from __future__ import annotations

import structlog

from .alpaca_broker import AlpacaBroker
from .base import BrokerInterface
from .paper_broker import PaperBroker

logger = structlog.get_logger(__name__)


class BrokerFactory:
    VALID_MODES = ("paper", "live", "backtest")

    def __init__(self, mode: str = "paper", initial_cash: float = 100_000.0):
        if mode not in self.VALID_MODES:
            raise ValueError(f"invalid broker mode: {mode!r}, must be one of {self.VALID_MODES}")
        self._mode = mode
        self._paper = PaperBroker(initial_cash=initial_cash) if mode == "backtest" else None
        self._alpaca = AlpacaBroker() if mode in ("paper", "live") else None

    def get_broker(self, symbol: str | None = None) -> BrokerInterface:
        if self._mode == "backtest":
            return self._paper  # type: ignore[return-value]
        return self._alpaca  # type: ignore[return-value]

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def paper_broker(self) -> PaperBroker | None:
        return self._paper


def create_broker(mode: str = "paper", initial_cash: float = 100_000.0) -> BrokerFactory:
    logger.info("broker_factory_created", mode=mode, initial_cash=initial_cash)
    return BrokerFactory(mode=mode, initial_cash=initial_cash)
