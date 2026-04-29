# / phase 7 tier 1: websocket streams — base + alpaca + coinbase modules.
# / protocol tests go through the stream's _handle_frame / _handle_message paths
# / directly so we don't need a real ws server.

from __future__ import annotations

import time

import pytest

from src.data.streams.alpaca_ws import AlpacaStream
from src.data.streams.alpaca_ws import _parse_ts as alpaca_parse_ts
from src.data.streams.base import (
    CircuitBreaker,
    StreamBase,
    StreamState,
    Tick,
    TickBuffer,
)
from src.data.streams.coinbase_ws import CoinbaseStream

# -------------------- TickBuffer --------------------


@pytest.mark.asyncio
async def test_tick_buffer_push_and_latest():
    buf = TickBuffer(max_per_symbol=5)
    for i in range(3):
        await buf.push(Tick("AAPL", 100.0 + i, 10.0, i, "alpaca"))
    latest = await buf.latest("AAPL")
    assert latest is not None and latest.price == 102.0


@pytest.mark.asyncio
async def test_tick_buffer_bounded_drops_oldest():
    buf = TickBuffer(max_per_symbol=3)
    for i in range(10):
        await buf.push(Tick("AAPL", float(i), 1.0, i, "alpaca"))
    ticks = await buf.drain("AAPL")
    # / only last 3 survive
    assert len(ticks) == 3
    assert [t.price for t in ticks] == [7.0, 8.0, 9.0]


@pytest.mark.asyncio
async def test_tick_buffer_drain_clears():
    buf = TickBuffer(max_per_symbol=10)
    await buf.push(Tick("AAPL", 100.0, 1.0, 0, "alpaca"))
    first = await buf.drain("AAPL")
    assert len(first) == 1
    second = await buf.drain("AAPL")
    assert second == []


@pytest.mark.asyncio
async def test_tick_buffer_drain_all():
    buf = TickBuffer(max_per_symbol=10)
    await buf.push(Tick("AAPL", 100.0, 1.0, 0, "alpaca"))
    await buf.push(Tick("MSFT", 200.0, 1.0, 0, "alpaca"))
    out = await buf.drain_all()
    assert set(out.keys()) == {"AAPL", "MSFT"}
    # / after drain_all, both symbols are empty
    again = await buf.drain_all()
    assert again == {}


@pytest.mark.asyncio
async def test_tick_buffer_isolates_symbols():
    buf = TickBuffer(max_per_symbol=2)
    await buf.push(Tick("AAPL", 100.0, 1.0, 0, "alpaca"))
    await buf.push(Tick("MSFT", 200.0, 1.0, 0, "alpaca"))
    await buf.push(Tick("AAPL", 101.0, 1.0, 0, "alpaca"))
    await buf.push(Tick("AAPL", 102.0, 1.0, 0, "alpaca"))
    aapl = await buf.drain("AAPL")
    msft = await buf.drain("MSFT")
    # / eviction on AAPL didn't spill into MSFT
    assert len(aapl) == 2 and aapl[0].price == 101.0
    assert len(msft) == 1 and msft[0].price == 200.0


# -------------------- CircuitBreaker --------------------


def test_circuit_breaker_closed_by_default():
    cb = CircuitBreaker()
    assert cb.is_open is False


def test_circuit_breaker_opens_after_threshold_disconnects():
    cb = CircuitBreaker(threshold=3, window_s=300.0, reset_s=120.0)
    cb.record_disconnect()
    cb.record_disconnect()
    assert cb.is_open is False
    cb.record_disconnect()
    assert cb.is_open is True


def test_circuit_breaker_stays_closed_if_disconnects_outside_window(monkeypatch):
    cb = CircuitBreaker(threshold=3, window_s=60.0, reset_s=120.0)
    base = [1000.0]

    def fake_mono() -> float:
        return base[0]

    monkeypatch.setattr("src.data.streams.base.time.monotonic", fake_mono)
    cb.record_disconnect()
    base[0] += 70.0  # / first event slides out of window
    cb.record_disconnect()
    base[0] += 70.0
    cb.record_disconnect()
    # / only 1 event is in the rolling window at any time
    assert cb.is_open is False


def test_circuit_breaker_record_healthy_clears_when_empty():
    cb = CircuitBreaker()
    cb.record_healthy()
    assert cb.is_open is False


# -------------------- StreamState --------------------


def test_stream_state_snapshot_shape():
    s = StreamState(name="x", total_ticks=5)
    snap = s.snapshot()
    for key in ("name", "status", "connected_at", "last_tick_age_s",
                "reconnect_attempts", "consecutive_errors", "total_ticks",
                "last_error", "circuit_open"):
        assert key in snap
    assert snap["total_ticks"] == 5
    # / no ticks yet => None
    assert snap["last_tick_age_s"] is None


# -------------------- StreamBase (dummy subclass) --------------------


class _DummyStream(StreamBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connect_calls = 0
        self.ticks_to_emit: list[Tick] = []

    @property
    def name(self) -> str:
        return "dummy"

    async def _connect_and_consume(self) -> None:
        self.connect_calls += 1
        self._mark_connected()
        for tick in self.ticks_to_emit:
            await self._emit(tick)
        # / then "disconnect" cleanly
        return


@pytest.mark.asyncio
async def test_stream_base_emit_updates_state_and_buffer():
    buf = TickBuffer()
    stream = _DummyStream(["AAPL"], buf)
    await stream._emit(Tick("AAPL", 100.0, 1.0, 0, "dummy"))
    assert stream.state.total_ticks == 1
    assert stream.state.last_tick_at is not None
    latest = await buf.latest("AAPL")
    assert latest is not None and latest.price == 100.0


@pytest.mark.asyncio
async def test_stream_base_emit_fires_callback():
    buf = TickBuffer()
    callback_calls: list[Tick] = []

    def cb(t: Tick) -> None:
        callback_calls.append(t)

    stream = _DummyStream(["AAPL"], buf, on_tick=cb)
    await stream._emit(Tick("AAPL", 100.0, 1.0, 0, "dummy"))
    assert len(callback_calls) == 1


@pytest.mark.asyncio
async def test_stream_base_emit_swallows_callback_errors():
    buf = TickBuffer()

    def broken_cb(t: Tick) -> None:
        raise RuntimeError("broken")

    stream = _DummyStream(["AAPL"], buf, on_tick=broken_cb)
    # / must not raise
    await stream._emit(Tick("AAPL", 100.0, 1.0, 0, "dummy"))
    assert stream.state.total_ticks == 1


@pytest.mark.asyncio
async def test_stream_base_mark_connected_records_healthy():
    buf = TickBuffer()
    stream = _DummyStream(["AAPL"], buf)
    stream._mark_connected()
    assert stream.state.status == "connected"
    assert stream.state.connected_at is not None


# -------------------- AlpacaStream frame handling --------------------


@pytest.mark.asyncio
async def test_alpaca_trade_frame_emits_tick():
    buf = TickBuffer()
    s = AlpacaStream(["AAPL"], buf)
    await s._handle_frame({
        "T": "t", "S": "AAPL", "p": 182.5, "s": 100,
        "t": "2026-04-20T13:30:00.123Z",
    })
    latest = await buf.latest("AAPL")
    assert latest is not None
    assert latest.price == 182.5
    assert latest.volume == 100.0
    assert latest.vendor == "alpaca"


@pytest.mark.asyncio
async def test_alpaca_quote_frame_emits_midprice():
    buf = TickBuffer()
    s = AlpacaStream(["AAPL"], buf)
    await s._handle_frame({
        "T": "q", "S": "AAPL", "bp": 100.0, "ap": 101.0,
        "t": "2026-04-20T13:30:00Z",
    })
    latest = await buf.latest("AAPL")
    assert latest is not None
    assert latest.price == pytest.approx(100.5)
    assert latest.volume is None


@pytest.mark.asyncio
async def test_alpaca_error_frame_does_not_emit():
    buf = TickBuffer()
    s = AlpacaStream(["AAPL"], buf)
    await s._handle_frame({"T": "error", "code": 407, "msg": "bad"})
    assert (await buf.latest("AAPL")) is None


@pytest.mark.asyncio
async def test_alpaca_missing_fields_skipped():
    buf = TickBuffer()
    s = AlpacaStream(["AAPL"], buf)
    await s._handle_frame({"T": "t", "S": "AAPL"})  # / no price
    await s._handle_frame({"T": "q", "S": "AAPL", "bp": 100.0})  # / no ask
    assert (await buf.latest("AAPL")) is None


def test_alpaca_parse_ts_rfc3339_nanos():
    # / alpaca emits nanosecond precision — python can't parse that directly
    ts = alpaca_parse_ts("2026-04-20T13:30:00.123456789Z")
    assert isinstance(ts, int) and ts > 0


def test_alpaca_parse_ts_none_fallback():
    ts = alpaca_parse_ts(None)
    # / fallback to now(), but should at least be in the valid range
    now_ms = int(time.time() * 1000)
    assert abs(ts - now_ms) < 5000


def test_alpaca_is_connected_ack_list_form():
    assert AlpacaStream._is_connected_ack([{"T": "success", "msg": "connected"}])
    assert not AlpacaStream._is_connected_ack([{"T": "error"}])


def test_alpaca_is_auth_ack_list_form():
    assert AlpacaStream._is_auth_ack([{"T": "success", "msg": "authenticated"}])
    assert not AlpacaStream._is_auth_ack([{"T": "success", "msg": "connected"}])


# -------------------- CoinbaseStream message handling --------------------


@pytest.mark.asyncio
async def test_coinbase_ticker_message_emits_tick():
    buf = TickBuffer()
    s = CoinbaseStream(["BTC-USD"], buf)
    await s._handle_message({
        "channel": "ticker",
        "timestamp": "2026-04-20T13:30:00.000Z",
        "events": [{"type": "update", "tickers": [
            {"product_id": "BTC-USD", "price": "70123.45",
             "volume_24_h": "12345.6"},
        ]}],
    })
    latest = await buf.latest("BTC-USD")
    assert latest is not None
    assert latest.price == pytest.approx(70123.45)
    assert latest.volume == pytest.approx(12345.6)
    assert latest.vendor == "coinbase"


@pytest.mark.asyncio
async def test_coinbase_non_ticker_channel_ignored():
    buf = TickBuffer()
    s = CoinbaseStream(["BTC-USD"], buf)
    await s._handle_message({"channel": "subscriptions", "events": []})
    assert (await buf.latest("BTC-USD")) is None


@pytest.mark.asyncio
async def test_coinbase_bad_price_skipped():
    buf = TickBuffer()
    s = CoinbaseStream(["BTC-USD"], buf)
    await s._handle_message({
        "channel": "ticker",
        "events": [{"tickers": [
            {"product_id": "BTC-USD", "price": "not-a-number"},
        ]}],
    })
    assert (await buf.latest("BTC-USD")) is None


@pytest.mark.asyncio
async def test_coinbase_missing_product_skipped():
    buf = TickBuffer()
    s = CoinbaseStream(["BTC-USD"], buf)
    await s._handle_message({
        "channel": "ticker",
        "events": [{"tickers": [{"price": "100.0"}]}],  # / no product_id
    })
    assert (await buf.latest("BTC-USD")) is None


@pytest.mark.asyncio
async def test_coinbase_multiple_tickers_in_one_event():
    buf = TickBuffer()
    s = CoinbaseStream(["BTC-USD", "ETH-USD"], buf)
    await s._handle_message({
        "channel": "ticker",
        "events": [{"tickers": [
            {"product_id": "BTC-USD", "price": "70000"},
            {"product_id": "ETH-USD", "price": "3500"},
        ]}],
    })
    btc = await buf.latest("BTC-USD")
    eth = await buf.latest("ETH-USD")
    assert btc is not None and btc.price == 70000.0
    assert eth is not None and eth.price == 3500.0


@pytest.mark.asyncio
async def test_coinbase_error_channel_logs_but_no_raise():
    buf = TickBuffer()
    s = CoinbaseStream(["BTC-USD"], buf)
    # / must not raise
    await s._handle_message({"channel": "error", "message": "bad subscription"})
    assert (await buf.latest("BTC-USD")) is None
