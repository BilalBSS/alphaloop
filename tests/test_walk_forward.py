# / tier 3 priority 1: walk-forward oos testing
# / walk-forward is how evolution decides if a mutation actually generalizes.
# / bug here = strategies promoted on in-sample noise → blown stops live.

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest

from src.strategies.walk_forward import (
    WalkForwardResult,
    walk_forward_test,
)


def _bars(n: int, start: str = "2024-01-01", base: float = 100.0, vol: float = 0.01):
    # / simple synthetic ohlcv series so walk_forward has something to slice
    rng = np.random.default_rng(7)
    dates = pd.date_range(start=start, periods=n, freq="D")
    rets = rng.normal(0.0005, vol, n)
    close = base * np.cumprod(1 + rets)
    return pd.DataFrame({
        "open": close * 0.999,
        "high": close * 1.005,
        "low": close * 0.995,
        "close": close,
        "volume": 1_000_000 + rng.integers(-50_000, 50_000, n),
    }, index=dates)


class _FakeBT:
    # / mimics BacktestResult fields we read (sharpe_ratio, total_return_pct)
    def __init__(self, sharpe: float, ret: float):
        self.sharpe_ratio = sharpe
        self.total_return_pct = ret


class _FakeStrategy:
    strategy_id = "wf_test"


class TestWalkForwardResultDataclass:
    def test_defaults(self):
        r = WalkForwardResult(strategy_id="x")
        assert r.strategy_id == "x"
        assert r.num_windows == 0
        assert r.oos_results == []
        assert r.avg_oos_sharpe == 0.0
        assert r.avg_oos_return == 0.0
        assert r.is_degradation is False


class TestWalkForwardTest:
    @pytest.mark.asyncio
    async def test_empty_market_data_returns_empty_result(self):
        result = await walk_forward_test(_FakeStrategy(), {})
        assert isinstance(result, WalkForwardResult)
        assert result.num_windows == 5
        assert result.oos_results == []

    @pytest.mark.asyncio
    async def test_insufficient_bars_short_circuits(self):
        # / < 50 bars per window → warn and bail
        data = {"AAPL": _bars(40)}
        with patch("src.strategies.walk_forward.run_backtest",
                   new_callable=AsyncMock) as m:
            result = await walk_forward_test(_FakeStrategy(), data, num_windows=5)
        assert result.oos_results == []
        m.assert_not_called()  # / never reached the backtest call

    @pytest.mark.asyncio
    async def test_five_window_split_invokes_backtest_per_window(self):
        # / 500 bars with num_windows=5 → 100 bars per window, 70 train / 30 oos.
        # / each window should call run_backtest once.
        data = {"AAPL": _bars(500)}
        calls: list[dict] = []

        async def fake_bt(strategy, md):
            calls.append({k: len(v) for k, v in md.items()})
            return _FakeBT(sharpe=1.0, ret=5.0)

        with patch("src.strategies.walk_forward.run_backtest", side_effect=fake_bt):
            result = await walk_forward_test(_FakeStrategy(), data, num_windows=5)

        assert len(result.oos_results) == 5
        assert len(calls) == 5
        # / every window's oos slice has at least 10 bars (the filter inside walk_forward)
        for c in calls:
            assert c["AAPL"] >= 10

    @pytest.mark.asyncio
    async def test_avg_metrics_match_mean_of_oos(self):
        data = {"AAPL": _bars(500)}
        sharpes = [0.8, 1.2, 0.4, 1.6, 1.0]
        rets = [2.0, 3.0, 1.0, 4.0, 2.5]
        call_count = [0]

        async def fake_bt(strategy, md):
            i = call_count[0]
            call_count[0] += 1
            return _FakeBT(sharpe=sharpes[i], ret=rets[i])

        with patch("src.strategies.walk_forward.run_backtest", side_effect=fake_bt):
            result = await walk_forward_test(_FakeStrategy(), data, num_windows=5)

        assert result.avg_oos_sharpe == pytest.approx(np.mean(sharpes))
        assert result.avg_oos_return == pytest.approx(np.mean(rets))

    @pytest.mark.asyncio
    async def test_drops_oos_window_when_slice_too_small(self):
        # / train_pct=0.95 on 100-bar windows leaves only 5 oos bars — filter excludes
        # / via the `len(sliced) >= 10` gate, leaving no symbols in oos_data, so that
        # / window is skipped (no backtest call for it).
        data = {"AAPL": _bars(500)}
        hits = [0]

        async def fake_bt(strategy, md):
            hits[0] += 1
            return _FakeBT(sharpe=1.0, ret=1.0)

        with patch("src.strategies.walk_forward.run_backtest", side_effect=fake_bt):
            result = await walk_forward_test(
                _FakeStrategy(), data, num_windows=5, train_pct=0.95,
            )
        # / all 5 windows produce too-small oos slices — result is empty
        assert result.oos_results == []
        assert hits[0] == 0

    @pytest.mark.asyncio
    async def test_multi_symbol_universe(self):
        data = {"AAPL": _bars(500), "MSFT": _bars(500, base=200.0)}

        async def fake_bt(strategy, md):
            assert set(md.keys()) == {"AAPL", "MSFT"}
            return _FakeBT(sharpe=1.0, ret=2.0)

        with patch("src.strategies.walk_forward.run_backtest", side_effect=fake_bt):
            result = await walk_forward_test(_FakeStrategy(), data, num_windows=5)
        assert len(result.oos_results) == 5

    @pytest.mark.asyncio
    async def test_train_test_ratio_respected(self):
        # / 1000 bars / 4 windows = 250 per window. train_pct=0.6 → train 150, oos 100.
        # / verify oos slice has ~100 bars for each window.
        data = {"AAPL": _bars(1000)}
        window_oos_lens: list[int] = []

        async def fake_bt(strategy, md):
            window_oos_lens.append(len(md["AAPL"]))
            return _FakeBT(sharpe=0.5, ret=1.0)

        with patch("src.strategies.walk_forward.run_backtest", side_effect=fake_bt):
            await walk_forward_test(_FakeStrategy(), data, num_windows=4, train_pct=0.6)

        # / each oos slice should be roughly 100 bars (allow ±5 for int rounding +
        # / inclusive-end-date behavior)
        assert len(window_oos_lens) == 4
        for n in window_oos_lens:
            assert 95 <= n <= 105
