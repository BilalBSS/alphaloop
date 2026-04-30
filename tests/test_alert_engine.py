# / tests for isolated alert engine — price-cross detection, discord batching, error isolation

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.agents import alert_engine

# / ---------------------------------------------------------------------------
# / helpers
# / ---------------------------------------------------------------------------

def _alert(alert_id: int, symbol: str, price: float, direction: str, status: str = "active") -> dict:
    return {
        "id": alert_id,
        "symbol": symbol,
        "price": price,
        "direction": direction,
        "label": None,
        "status": status,
        "last_check": None,
        "fired_at": None,
        "created_at": "2026-03-20T10:00:00",
    }


def _broker_with_prices(prices: dict[str, float]):
    broker = MagicMock()

    async def _get_price(symbol: str):
        if symbol not in prices:
            raise RuntimeError(f"no price for {symbol}")
        return prices[symbol]

    broker.get_price = _get_price
    return broker


# / ---------------------------------------------------------------------------
# / cross detection
# / ---------------------------------------------------------------------------

class TestCrossDetection:
    def test_above_cross_fires_on_transition(self):
        # / prev 99, new 101, target 100 → fires
        assert alert_engine._is_crossed("above", 99.0, 101.0, 100.0) is True

    def test_above_no_cross_when_below(self):
        # / prev 95, new 99, target 100 → no fire
        assert alert_engine._is_crossed("above", 95.0, 99.0, 100.0) is False

    def test_above_no_refire_when_already_above(self):
        # / already above last tick — must not re-fire
        assert alert_engine._is_crossed("above", 105.0, 110.0, 100.0) is False

    def test_above_cold_start_fires_at_or_above_target(self):
        # / first observation with prev=None fires when current meets/exceeds target
        assert alert_engine._is_crossed("above", None, 105.0, 100.0) is True
        assert alert_engine._is_crossed("above", None, 99.0, 100.0) is False

    def test_below_cross_fires_on_transition(self):
        # / prev 101, new 99, target 100 → fires
        assert alert_engine._is_crossed("below", 101.0, 99.0, 100.0) is True

    def test_below_no_cross_when_above(self):
        # / prev 105, new 104, target 100 → no fire
        assert alert_engine._is_crossed("below", 105.0, 104.0, 100.0) is False

    def test_below_no_refire_when_already_below(self):
        assert alert_engine._is_crossed("below", 95.0, 90.0, 100.0) is False

    def test_below_cold_start_fires_at_or_below(self):
        assert alert_engine._is_crossed("below", None, 95.0, 100.0) is True
        assert alert_engine._is_crossed("below", None, 101.0, 100.0) is False

    def test_unknown_direction_never_fires(self):
        assert alert_engine._is_crossed("sideways", 95.0, 105.0, 100.0) is False


# / ---------------------------------------------------------------------------
# / check_and_fire tick behavior
# / ---------------------------------------------------------------------------

class TestCheckAndFire:
    @pytest.mark.asyncio
    async def test_above_cross_fires_and_marks(self):
        pool = MagicMock()
        broker = _broker_with_prices({"AAPL": 101.0})
        fired_list: list[dict] = []

        async def fake_list(p, symbol=None, status=None):
            return [_alert(1, "AAPL", 100.0, "above")]

        async def fake_mark_fired(p, alert_id, when):
            row = _alert(alert_id, "AAPL", 100.0, "above", status="fired")
            row["fired_at"] = when.isoformat() if hasattr(when, "isoformat") else str(when)
            return row

        async def fake_mark_checked(p, ids, when):
            return None

        prev_prices = {"AAPL": 99.0}

        with (
            patch.object(alert_engine.alerts_mod, "list_alerts", side_effect=fake_list),
            patch.object(alert_engine.alerts_mod, "mark_fired", side_effect=fake_mark_fired),
            patch.object(alert_engine.alerts_mod, "mark_checked", side_effect=fake_mark_checked),
        ):
            fired_list = await alert_engine.check_and_fire(
                pool, broker, None, None, prev_prices,
            )

        assert len(fired_list) == 1
        assert fired_list[0]["id"] == 1
        assert fired_list[0]["current_price"] == 101.0
        assert prev_prices["AAPL"] == 101.0

    @pytest.mark.asyncio
    async def test_below_cross_fires_and_marks(self):
        pool = MagicMock()
        broker = _broker_with_prices({"AAPL": 99.0})

        async def fake_list(p, symbol=None, status=None):
            return [_alert(2, "AAPL", 100.0, "below")]

        async def fake_mark_fired(p, alert_id, when):
            return _alert(alert_id, "AAPL", 100.0, "below", status="fired")

        async def fake_mark_checked(p, ids, when):
            return None

        prev_prices = {"AAPL": 101.0}

        with (
            patch.object(alert_engine.alerts_mod, "list_alerts", side_effect=fake_list),
            patch.object(alert_engine.alerts_mod, "mark_fired", side_effect=fake_mark_fired),
            patch.object(alert_engine.alerts_mod, "mark_checked", side_effect=fake_mark_checked),
        ):
            fired_list = await alert_engine.check_and_fire(
                pool, broker, None, None, prev_prices,
            )

        assert len(fired_list) == 1

    @pytest.mark.asyncio
    async def test_no_cross_no_fire(self):
        pool = MagicMock()
        broker = _broker_with_prices({"AAPL": 104.0})
        mark_calls = 0

        async def fake_list(p, symbol=None, status=None):
            return [_alert(3, "AAPL", 100.0, "below")]

        async def fake_mark_fired(p, alert_id, when):
            nonlocal mark_calls
            mark_calls += 1
            return _alert(alert_id, "AAPL", 100.0, "below", status="fired")

        async def fake_mark_checked(p, ids, when):
            return None

        prev_prices = {"AAPL": 105.0}

        with (
            patch.object(alert_engine.alerts_mod, "list_alerts", side_effect=fake_list),
            patch.object(alert_engine.alerts_mod, "mark_fired", side_effect=fake_mark_fired),
            patch.object(alert_engine.alerts_mod, "mark_checked", side_effect=fake_mark_checked),
        ):
            fired_list = await alert_engine.check_and_fire(
                pool, broker, None, None, prev_prices,
            )

        assert fired_list == []
        assert mark_calls == 0

    @pytest.mark.asyncio
    async def test_multiple_alerts_one_symbol_single_price_fetch(self):
        # / three alerts on AAPL — broker.get_price should be called exactly once
        pool = MagicMock()
        call_count = 0

        async def _get_price(symbol):
            nonlocal call_count
            call_count += 1
            return 101.0

        broker = MagicMock()
        broker.get_price = _get_price

        alerts_list = [
            _alert(1, "AAPL", 100.0, "above"),
            _alert(2, "AAPL", 99.5, "above"),
            _alert(3, "AAPL", 105.0, "above"),
        ]

        async def fake_list(p, symbol=None, status=None):
            return alerts_list

        async def fake_mark_fired(p, alert_id, when):
            return _alert(alert_id, "AAPL", 100.0, "above", status="fired")

        async def fake_mark_checked(p, ids, when):
            return None

        prev_prices = {"AAPL": 99.0}

        with (
            patch.object(alert_engine.alerts_mod, "list_alerts", side_effect=fake_list),
            patch.object(alert_engine.alerts_mod, "mark_fired", side_effect=fake_mark_fired),
            patch.object(alert_engine.alerts_mod, "mark_checked", side_effect=fake_mark_checked),
        ):
            fired_list = await alert_engine.check_and_fire(
                pool, broker, None, None, prev_prices,
            )

        assert call_count == 1
        # / alerts at 100.0 and 99.5 cross from prev 99 -> 101, alert at 105 does not
        assert len(fired_list) == 2

    @pytest.mark.asyncio
    async def test_discord_batches_fires_into_single_post(self):
        # / verify all fires go out in ONE api_post call per tick
        pool = MagicMock()
        broker = _broker_with_prices({"AAPL": 101.0, "MSFT": 200.0})

        async def fake_list(p, symbol=None, status=None):
            return [
                _alert(1, "AAPL", 100.0, "above"),
                _alert(2, "MSFT", 199.0, "above"),
            ]

        async def fake_mark_fired(p, alert_id, when):
            return _alert(alert_id, "?", 0, "above", status="fired")

        async def fake_mark_checked(p, ids, when):
            return None

        post_calls: list[dict] = []

        async def fake_post(url, json=None, timeout=5.0):
            post_calls.append({"url": url, "json": json})

            class R:
                pass
            return R()

        prev_prices = {"AAPL": 99.0, "MSFT": 195.0}

        with (
            patch.object(alert_engine.alerts_mod, "list_alerts", side_effect=fake_list),
            patch.object(alert_engine.alerts_mod, "mark_fired", side_effect=fake_mark_fired),
            patch.object(alert_engine.alerts_mod, "mark_checked", side_effect=fake_mark_checked),
            patch.object(alert_engine, "api_post", side_effect=fake_post),
        ):
            await alert_engine.check_and_fire(
                pool, broker, None, "https://webhook.test/abc", prev_prices,
            )

        assert len(post_calls) == 1
        body = post_calls[0]["json"]
        assert "embeds" in body
        assert len(body["embeds"]) == 2

    @pytest.mark.asyncio
    async def test_discord_overflow_uses_content_line(self):
        # / more than 10 fires → embeds capped at 10, content shows overflow count
        fires = [_alert(i, "AAPL", 100.0 + i, "above") for i in range(1, 13)]
        body = alert_engine._build_discord_body(fires)
        assert len(body["embeds"]) == 10
        assert "content" in body
        assert "+2" in body["content"]

    @pytest.mark.asyncio
    async def test_mark_fired_double_fire_returns_none_skips(self):
        # / simulate lost race — mark_fired returns None, fired list stays empty
        pool = MagicMock()
        broker = _broker_with_prices({"AAPL": 101.0})

        async def fake_list(p, symbol=None, status=None):
            return [_alert(1, "AAPL", 100.0, "above")]

        async def fake_mark_fired(p, alert_id, when):
            return None  # / already fired by another racer

        async def fake_mark_checked(p, ids, when):
            return None

        prev_prices = {"AAPL": 99.0}

        with (
            patch.object(alert_engine.alerts_mod, "list_alerts", side_effect=fake_list),
            patch.object(alert_engine.alerts_mod, "mark_fired", side_effect=fake_mark_fired),
            patch.object(alert_engine.alerts_mod, "mark_checked", side_effect=fake_mark_checked),
        ):
            fired_list = await alert_engine.check_and_fire(
                pool, broker, None, None, prev_prices,
            )

        assert fired_list == []

    @pytest.mark.asyncio
    async def test_broker_price_fetch_error_isolated(self):
        # / one symbol raises, another still fires — loop must not die
        pool = MagicMock()

        async def _get_price(symbol):
            if symbol == "BAD":
                raise RuntimeError("broker down")
            return 101.0

        broker = MagicMock()
        broker.get_price = _get_price

        async def fake_list(p, symbol=None, status=None):
            return [
                _alert(1, "BAD", 100.0, "above"),
                _alert(2, "AAPL", 100.0, "above"),
            ]

        async def fake_mark_fired(p, alert_id, when):
            return _alert(alert_id, "AAPL", 100.0, "above", status="fired")

        async def fake_mark_checked(p, ids, when):
            return None

        prev_prices = {"AAPL": 99.0, "BAD": 99.0}

        with (
            patch.object(alert_engine.alerts_mod, "list_alerts", side_effect=fake_list),
            patch.object(alert_engine.alerts_mod, "mark_fired", side_effect=fake_mark_fired),
            patch.object(alert_engine.alerts_mod, "mark_checked", side_effect=fake_mark_checked),
        ):
            fired_list = await alert_engine.check_and_fire(
                pool, broker, None, None, prev_prices,
            )

        # / AAPL still fires, BAD skipped
        assert len(fired_list) == 1
        assert fired_list[0]["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_ws_broadcast_each_fire(self):
        pool = MagicMock()
        broker = _broker_with_prices({"AAPL": 101.0, "MSFT": 200.0})
        broadcast_calls: list[tuple] = []

        async def fake_broadcast(event_type, data):
            broadcast_calls.append((event_type, data))

        async def fake_list(p, symbol=None, status=None):
            return [
                _alert(1, "AAPL", 100.0, "above"),
                _alert(2, "MSFT", 199.0, "above"),
            ]

        async def fake_mark_fired(p, alert_id, when):
            return _alert(alert_id, "?", 0, "above", status="fired")

        async def fake_mark_checked(p, ids, when):
            return None

        prev_prices = {"AAPL": 99.0, "MSFT": 195.0}

        with (
            patch.object(alert_engine.alerts_mod, "list_alerts", side_effect=fake_list),
            patch.object(alert_engine.alerts_mod, "mark_fired", side_effect=fake_mark_fired),
            patch.object(alert_engine.alerts_mod, "mark_checked", side_effect=fake_mark_checked),
        ):
            await alert_engine.check_and_fire(
                pool, broker, fake_broadcast, None, prev_prices,
            )

        assert len(broadcast_calls) == 2
        assert broadcast_calls[0][0] == "alert.triggered"
        assert "alert" in broadcast_calls[0][1]


# / ---------------------------------------------------------------------------
# / loop runner exit behavior
# / ---------------------------------------------------------------------------

class TestAlertLoopRunner:
    @pytest.mark.asyncio
    async def test_loop_exits_on_stop_event(self):
        # / stop_event set before start → loop exits without touching broker
        stop = asyncio.Event()
        stop.set()

        pool = MagicMock()
        broker = MagicMock()

        await alert_engine.alert_loop(
            pool, broker, ws_broadcast=None, interval_sec=1,
            webhook_url=None, stop_event=stop,
        )
        # / broker.get_price never called because stop short-circuits
        broker.get_price.assert_not_called() if hasattr(broker, "get_price") else None

    @pytest.mark.asyncio
    async def test_loop_one_tick_then_stops(self):
        # / after first tick runs, stop_event is set and the loop exits
        stop = asyncio.Event()
        pool = MagicMock()
        broker = _broker_with_prices({"AAPL": 101.0})
        tick_count = 0

        async def fake_check(pool_arg, broker_arg, ws, webhook, prev_prices):
            nonlocal tick_count
            tick_count += 1
            stop.set()
            return []

        with patch.object(alert_engine, "check_and_fire", side_effect=fake_check):
            await alert_engine.alert_loop(
                pool, broker, ws_broadcast=None, interval_sec=0,
                webhook_url=None, stop_event=stop,
            )

        assert tick_count == 1
