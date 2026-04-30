# / tests for broker factory

from __future__ import annotations

import pytest

from src.brokers.alpaca_broker import AlpacaBroker
from src.brokers.broker_factory import BrokerFactory, create_broker
from src.brokers.paper_broker import PaperBroker


class TestBrokerFactory:
    def test_paper_mode_uses_alpaca(self):
        # / paper mode uses AlpacaBroker (hits alpaca paper-api)
        factory = BrokerFactory(mode="paper")
        assert factory.mode == "paper"
        broker = factory.get_broker("AAPL")
        assert isinstance(broker, AlpacaBroker)

    def test_paper_mode_crypto(self):
        factory = BrokerFactory(mode="paper")
        broker = factory.get_broker("BTC-USD")
        assert isinstance(broker, AlpacaBroker)

    def test_live_mode(self):
        factory = BrokerFactory(mode="live")
        assert factory.mode == "live"
        broker = factory.get_broker("AAPL")
        assert isinstance(broker, AlpacaBroker)

    def test_backtest_mode_uses_paper_broker(self):
        # / backtest mode uses in-memory PaperBroker for simulation
        factory = BrokerFactory(mode="backtest")
        assert factory.mode == "backtest"
        broker = factory.get_broker("AAPL")
        assert isinstance(broker, PaperBroker)

    def test_paper_broker_property_backtest(self):
        factory = BrokerFactory(mode="backtest")
        assert factory.paper_broker is not None
        assert isinstance(factory.paper_broker, PaperBroker)

    def test_paper_broker_none_in_paper_and_live(self):
        # / paper + live both use alpaca, no in-memory broker
        assert BrokerFactory(mode="paper").paper_broker is None
        assert BrokerFactory(mode="live").paper_broker is None

    def test_initial_cash_backtest(self):
        factory = BrokerFactory(mode="backtest", initial_cash=50_000.0)
        broker = factory.get_broker()
        assert isinstance(broker, PaperBroker)
        assert broker.cash == 50_000.0


class TestCreateBroker:
    def test_convenience_function(self):
        factory = create_broker(mode="paper", initial_cash=75_000.0)
        assert isinstance(factory, BrokerFactory)
        assert factory.mode == "paper"


# ---------- deep tests ----------


class TestBrokerFactoryDeep:
    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="invalid broker mode"):
            BrokerFactory(mode="invalid")

    def test_case_sensitive_mode(self):
        with pytest.raises(ValueError, match="invalid broker mode"):
            BrokerFactory(mode="Paper")

    def test_get_broker_returns_same_type_consistently(self):
        factory = BrokerFactory(mode="paper")
        b1 = factory.get_broker("AAPL")
        b2 = factory.get_broker("MSFT")
        b3 = factory.get_broker("BTC-USD")
        assert type(b1) is type(b2) is type(b3)
        assert b1 is b2 is b3

    def test_mode_property_paper(self):
        factory = BrokerFactory(mode="paper")
        assert factory.mode == "paper"

    def test_mode_property_live(self):
        factory = BrokerFactory(mode="live")
        assert factory.mode == "live"
