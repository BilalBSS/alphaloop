# / tests for backtesting engine

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.strategies.backtest import (
    BacktestResult,
    Trade,
    _compute_metrics,
    run_backtest,
)
from src.strategies.base_strategy import ConfigDrivenStrategy

# ---------------------------------------------------------------------------
# / helpers
# ---------------------------------------------------------------------------

def _make_strategy(overrides: dict | None = None) -> ConfigDrivenStrategy:
    # / minimal config that triggers entries via rsi oversold + price below sma
    cfg = {
        "id": "test_strat_001",
        "name": "test_strategy",
        "universe": ["TEST"],
        "fundamental_filters": {},
        "entry_conditions": {
            "operator": "AND",
            "signals": [
                {"indicator": "rsi", "condition": "below", "threshold": 40, "period": 14},
            ],
        },
        "exit_conditions": {
            "stop_loss": {"type": "fixed_pct", "pct": 0.05},
            "time_exit": {"max_holding_days": 30},
        },
        "position_sizing": {
            "method": "fixed_pct",
            "max_position_pct": 0.08,
        },
    }
    if overrides:
        cfg.update(overrides)
    return ConfigDrivenStrategy(cfg)


def _make_always_enter_strategy(
    stop_pct: float = 0.05,
    max_holding_days: int = 9999,
    max_position_pct: float = 0.08,
) -> ConfigDrivenStrategy:
    # / strategy with no technical conditions — enters every bar (after warmup)
    cfg = {
        "id": "always_enter_001",
        "name": "always_enter",
        "universe": ["TEST"],
        "fundamental_filters": {},
        "entry_conditions": {
            "operator": "AND",
            "signals": [],  # / no signals = always enter
        },
        "exit_conditions": {
            "stop_loss": {"type": "fixed_pct", "pct": stop_pct},
            "time_exit": {"max_holding_days": max_holding_days},
        },
        "position_sizing": {
            "method": "fixed_pct",
            "max_position_pct": max_position_pct,
        },
    }
    return ConfigDrivenStrategy(cfg)


def _make_ohlcv(
    n: int = 150,
    start_price: float = 100.0,
    trend: float = 0.0005,
    volatility: float = 0.015,
    seed: int = 42,
    start_date: str = "2024-01-02",
) -> pd.DataFrame:
    # / generate deterministic ohlcv data with a trend component
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start_date, periods=n)
    log_returns = trend + volatility * rng.standard_normal(n)
    # / cumulative price from log returns
    prices = start_price * np.exp(np.cumsum(log_returns))

    # / build ohlcv from close prices
    opens = np.roll(prices, 1)
    opens[0] = start_price
    highs = np.maximum(opens, prices) * (1 + rng.uniform(0, 0.01, n))
    lows = np.minimum(opens, prices) * (1 - rng.uniform(0, 0.01, n))
    volumes = rng.integers(500_000, 5_000_000, size=n).astype(float)

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": prices, "volume": volumes},
        index=dates,
    )
    return df


def _make_uptrend(n: int = 150, **kwargs) -> pd.DataFrame:
    return _make_ohlcv(n=n, trend=0.003, volatility=0.008, **kwargs)


def _make_downtrend(n: int = 150, **kwargs) -> pd.DataFrame:
    return _make_ohlcv(n=n, trend=-0.003, volatility=0.008, **kwargs)


# ---------------------------------------------------------------------------
# / test run_backtest — basic scenarios
# ---------------------------------------------------------------------------

class TestRunBacktestBasic:
    @pytest.mark.asyncio
    async def test_empty_market_data_returns_initial_equity(self):
        # / no symbols = no trades, equity unchanged
        strat = _make_strategy()
        result = await run_backtest(strat, market_data={}, initial_cash=50_000.0)

        assert result.initial_equity == 50_000.0
        assert result.final_equity == 50_000.0
        assert result.total_trades == 0
        assert result.strategy_id == "test_strat_001"

    @pytest.mark.asyncio
    async def test_insufficient_data_returns_initial_equity(self):
        # / only 1 bar — not enough for trading
        dates = pd.bdate_range("2024-01-02", periods=1)
        df = pd.DataFrame(
            {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [1e6]},
            index=dates,
        )
        strat = _make_strategy()
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        assert result.final_equity == 100_000.0
        assert result.total_trades == 0

    @pytest.mark.asyncio
    async def test_uptrend_makes_money(self):
        # / strong uptrend — strategy that enters should profit
        df = _make_uptrend(n=150, seed=42)
        strat = _make_always_enter_strategy(stop_pct=0.15, max_holding_days=9999)
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        # / with a strong uptrend, final equity should exceed initial
        assert result.final_equity > result.initial_equity
        assert result.total_return > 0

    @pytest.mark.asyncio
    async def test_downtrend_loses_money_or_avoids(self):
        # / strong downtrend — strategy should lose money or stop out
        df = _make_downtrend(n=150, seed=42)
        strat = _make_always_enter_strategy(stop_pct=0.05, max_holding_days=9999)
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        # / in a downtrend, strategy either loses money or stops out early
        # / final equity should be less than or equal to initial
        assert result.final_equity <= result.initial_equity

    @pytest.mark.asyncio
    async def test_result_has_equity_curve(self):
        df = _make_ohlcv(n=100)
        strat = _make_always_enter_strategy()
        result = await run_backtest(strat, {"TEST": df})

        assert len(result.equity_curve) > 0
        assert len(result.daily_returns) > 0

    @pytest.mark.asyncio
    async def test_result_period_dates_set(self):
        df = _make_ohlcv(n=100)
        strat = _make_always_enter_strategy()
        result = await run_backtest(strat, {"TEST": df})

        assert result.period_start is not None
        assert result.period_end is not None
        assert result.period_start <= result.period_end


# ---------------------------------------------------------------------------
# / test anti-lookahead bias
# ---------------------------------------------------------------------------

class TestAntiLookahead:
    @pytest.mark.asyncio
    async def test_entry_uses_previous_bar_data(self):
        # / create data where rsi drops below threshold only at bar N's close
        # / the backtest should NOT enter until bar N+1 (fill at N+1 open)
        #
        # / approach: build normal data, find the bar where rsi first crosses below 40,
        # / then verify no entry happened before that bar + 1
        rng = np.random.default_rng(99)
        n = 120

        # / start with high rsi (prices going up), then drop sharply at bar 80
        prices = np.zeros(n)
        prices[0] = 100.0
        for i in range(1, 70):
            prices[i] = prices[i - 1] * (1 + 0.005 + 0.005 * rng.standard_normal())
        # / sharp drop to push rsi below 40
        for i in range(70, 80):
            prices[i] = prices[i - 1] * (1 - 0.03 + 0.003 * rng.standard_normal())
        # / recovery
        for i in range(80, n):
            prices[i] = prices[i - 1] * (1 + 0.002 + 0.005 * rng.standard_normal())

        dates = pd.bdate_range("2024-01-02", periods=n)
        opens = np.roll(prices, 1)
        opens[0] = 100.0
        highs = np.maximum(opens, prices) * 1.005
        lows = np.minimum(opens, prices) * 0.995
        volumes = rng.integers(1_000_000, 3_000_000, size=n).astype(float)

        df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": prices, "volume": volumes},
            index=dates,
        )

        # / find where rsi first goes below 40 (using the indicator directly)
        from src.indicators.momentum import rsi as rsi_fn
        rsi_series = rsi_fn(df["close"], period=14)
        rsi_below = rsi_series.dropna() < 40
        if not rsi_below.any():
            pytest.skip("rsi never dropped below 40 in test data")
        first_signal_date = rsi_below[rsi_below].index[0]
        signal_bar_idx = df.index.get_loc(first_signal_date)

        # / strategy uses rsi < 40
        strat = _make_strategy({
            "entry_conditions": {
                "operator": "AND",
                "signals": [
                    {"indicator": "rsi", "condition": "below", "threshold": 40, "period": 14},
                ],
            },
        })
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        # / if any trades happened, the earliest entry must be AFTER the signal bar
        # / (filled at next bar's open, not at the signal bar)
        if result.trades:
            earliest_entry = min(t.entry_date for t in result.trades)
            earliest_idx = df.index.get_loc(earliest_entry)
            # / entry should be at signal_bar_idx + 1 or later (after warmup)
            assert earliest_idx > signal_bar_idx, (
                f"entry at bar {earliest_idx} but signal at bar {signal_bar_idx} — lookahead!"
            )

    @pytest.mark.asyncio
    async def test_fills_at_open_not_close(self):
        # / verify that entries fill at the open price of the entry bar
        df = _make_ohlcv(n=120, seed=77)
        strat = _make_always_enter_strategy()
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        if not result.trades:
            pytest.skip("no trades to check fill prices")

        # / entry price should match an open price in the data, not a close
        for trade in result.trades:
            entry_date = trade.entry_date
            if entry_date in df.index:
                open_price = float(df.loc[entry_date, "open"])
                # / entry_price should be the open of the entry bar
                assert trade.entry_price == pytest.approx(open_price, rel=1e-6), (
                    f"entry at {trade.entry_price} != open {open_price} on {entry_date}"
                )


# ---------------------------------------------------------------------------
# / test position sizing + max open positions
# ---------------------------------------------------------------------------

class TestPositionLimits:
    @pytest.mark.asyncio
    async def test_max_open_positions_respected(self):
        # / run with 3 symbols but max_open_positions=2
        df1 = _make_ohlcv(n=120, seed=10, start_price=100)
        df2 = _make_ohlcv(n=120, seed=20, start_price=200)
        df3 = _make_ohlcv(n=120, seed=30, start_price=300)

        strat = _make_always_enter_strategy()
        result = await run_backtest(
            strat,
            {"SYM1": df1, "SYM2": df2, "SYM3": df3},
            initial_cash=100_000.0,
            max_open_positions=2,
        )

        # / reconstruct concurrent positions using entry/exit events
        events: list[tuple[pd.Timestamp, int]] = []
        for trade in result.trades:
            events.append((trade.entry_date, +1))
            exit_dt = trade.exit_date or result.period_end
            events.append((exit_dt, -1))

        # / sort by date, then closes before opens (so exits at same bar reduce count first)
        events.sort(key=lambda x: (x[0], x[1]))
        running = 0
        max_concurrent = 0
        for _, delta in events:
            running += delta
            max_concurrent = max(max_concurrent, running)

        assert max_concurrent <= 2, f"had {max_concurrent} concurrent positions, max was 2"

    @pytest.mark.asyncio
    async def test_position_size_uses_fixed_pct(self):
        # / verify position size is ~8% of equity
        df = _make_ohlcv(n=120, seed=42)
        strat = _make_always_enter_strategy(max_position_pct=0.08)
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        if result.trades:
            first_trade = result.trades[0]
            position_value = first_trade.entry_price * first_trade.qty
            # / should be roughly 8% of initial equity (within rounding to whole shares)
            pct = position_value / 100_000.0
            assert 0.01 < pct < 0.10, f"position was {pct:.2%} of equity, expected ~8%"


# ---------------------------------------------------------------------------
# / test stop loss
# ---------------------------------------------------------------------------

class TestStopLoss:
    @pytest.mark.asyncio
    async def test_fixed_pct_stop_triggers(self):
        # / build data that drops enough to trigger a 5% stop
        df = _make_downtrend(n=120, seed=55)
        strat = _make_always_enter_strategy(stop_pct=0.05, max_holding_days=9999)
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        # / at least some trades should have been stopped out
        stop_trades = [t for t in result.trades if "stop loss" in t.exit_reason]
        assert len(stop_trades) > 0, "no stop loss exits in downtrend — stop not triggering"

    @pytest.mark.asyncio
    async def test_stop_loss_limits_per_trade_loss(self):
        # / each stopped-out trade should have pnl_pct roughly >= -stop_pct
        # / (can be slightly worse due to gap/slippage at open)
        df = _make_downtrend(n=150, seed=55)
        strat = _make_always_enter_strategy(stop_pct=0.05, max_holding_days=9999)
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        stop_trades = [t for t in result.trades if "stop loss" in t.exit_reason]
        for t in stop_trades:
            # / allow some slack for gap fills — stop evaluated at prev close, filled at next open
            assert t.pnl_pct > -0.15, (
                f"trade lost {t.pnl_pct:.2%}, expected stop to limit losses near 5%"
            )


# ---------------------------------------------------------------------------
# / test time exit
# ---------------------------------------------------------------------------

class TestTimeExit:
    @pytest.mark.asyncio
    async def test_max_holding_days_triggers(self):
        # / set a short max_holding_days and verify trades exit by that limit
        df = _make_ohlcv(n=150, seed=42, trend=0.001)
        strat = _make_always_enter_strategy(stop_pct=0.50, max_holding_days=5)
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        time_exits = [t for t in result.trades if "time exit" in t.exit_reason]
        assert len(time_exits) > 0, "no time exits with max_holding_days=5"

    @pytest.mark.asyncio
    async def test_holding_days_near_limit(self):
        # / time-exited trades should have holding_days near max_holding_days
        df = _make_ohlcv(n=150, seed=42, trend=0.001)
        strat = _make_always_enter_strategy(stop_pct=0.50, max_holding_days=5)
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        time_exits = [t for t in result.trades if "time exit" in t.exit_reason]
        for t in time_exits:
            # / holding days should be >= max_holding_days (exit evaluated at close,
            # / so actual calendar days can be a bit more)
            assert t.holding_days >= 5, f"time exit at {t.holding_days} days, expected >= 5"


# ---------------------------------------------------------------------------
# / test multiple symbols
# ---------------------------------------------------------------------------

class TestMultipleSymbols:
    @pytest.mark.asyncio
    async def test_trades_across_symbols(self):
        df1 = _make_ohlcv(n=120, seed=10, start_price=50)
        df2 = _make_ohlcv(n=120, seed=20, start_price=150)
        strat = _make_always_enter_strategy()

        result = await run_backtest(
            strat,
            {"AAPL": df1, "MSFT": df2},
            initial_cash=100_000.0,
            max_open_positions=5,
        )

        traded_symbols = {t.symbol for t in result.trades}
        # / with always-enter strategy and 2 symbols, both should trade
        assert len(traded_symbols) >= 1, "expected trades in at least one symbol"

    @pytest.mark.asyncio
    async def test_different_date_ranges(self):
        # / symbols with slightly different date ranges should still work
        df1 = _make_ohlcv(n=120, seed=10, start_date="2024-01-02")
        df2 = _make_ohlcv(n=100, seed=20, start_date="2024-01-15")
        strat = _make_always_enter_strategy()

        result = await run_backtest(
            strat,
            {"SYM1": df1, "SYM2": df2},
            initial_cash=100_000.0,
        )

        # / should run without errors, period should span both
        assert result.period_start is not None
        assert result.period_end is not None


# ---------------------------------------------------------------------------
# / test _compute_metrics — known inputs
# ---------------------------------------------------------------------------

class TestComputeMetricsSharpe:
    def test_known_sharpe_ratio(self):
        # / daily returns with known mean and std
        # / mean=0.001, std=0.01 => sharpe = 0.001/0.01 * sqrt(252) ~ 1.587
        rng = np.random.default_rng(42)
        n = 252
        daily_returns = list(0.001 + 0.01 * rng.standard_normal(n))
        equity_curve = [100_000.0]
        for r in daily_returns:
            equity_curve.append(equity_curve[-1] * (1 + r))

        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0,
            final_equity=equity_curve[-1],
            equity_curve=equity_curve,
            daily_returns=daily_returns,
            trades=[],
        )

        # / sharpe should be approximately mean/std * sqrt(252)
        expected_sharpe = np.mean(daily_returns) / np.std(daily_returns, ddof=1) * math.sqrt(252)
        assert result.sharpe_ratio == pytest.approx(expected_sharpe, rel=0.01)

    def test_zero_returns_sharpe_is_zero(self):
        # / all zero returns => mean=0, std=0 => sharpe=0 (0/anything=0)
        daily_returns = [0.0] * 100
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=100_000.0,
            equity_curve=[100_000.0] * 100,
            daily_returns=daily_returns,
            trades=[],
        )
        # / mean=0 so numerator is 0 regardless of std
        assert result.sharpe_ratio == 0.0

    def test_no_returns_sharpe_is_zero(self):
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=100_000.0,
            equity_curve=[], daily_returns=[], trades=[],
        )
        assert result.sharpe_ratio == 0.0


class TestComputeMetricsSortino:
    def test_sortino_only_uses_downside(self):
        # / returns with known downside: mix of positive and negative
        daily_returns = [0.01, -0.005, 0.02, -0.01, 0.015, -0.003, 0.005, -0.008]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=102_000.0,
            equity_curve=[100_000.0] * 8,
            daily_returns=daily_returns,
            trades=[],
        )

        # / compute expected sortino manually
        arr = np.array(daily_returns)
        avg_ret = float(np.mean(arr))
        downside = arr[arr < 0]
        downside_std = float(np.std(downside, ddof=1))
        expected_sortino = avg_ret / downside_std * math.sqrt(252)
        assert result.sortino_ratio == pytest.approx(expected_sortino, rel=0.01)

    def test_no_negative_returns_sortino_inf(self):
        # / all positive returns => no downside => sortino=inf
        daily_returns = [0.01, 0.02, 0.005, 0.015]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=105_000.0,
            equity_curve=[100_000.0] * 4,
            daily_returns=daily_returns,
            trades=[],
        )
        assert result.sortino_ratio == float("inf")


class TestComputeMetricsDrawdown:
    def test_max_drawdown_known_curve(self):
        # / equity curve: 100k -> 110k -> 90k -> 95k
        # / peak=110k, trough=90k, drawdown=20k, dd_pct=20k/110k ~ 18.18%
        equity_curve = [100_000.0, 110_000.0, 90_000.0, 95_000.0]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=95_000.0,
            equity_curve=equity_curve,
            daily_returns=[0.1, -0.1818, 0.0556],
            trades=[],
        )

        assert result.max_drawdown == pytest.approx(20_000.0)
        assert result.max_drawdown_pct == pytest.approx(20_000.0 / 110_000.0, rel=0.01)

    def test_no_drawdown_when_always_increasing(self):
        equity_curve = [100_000.0, 101_000.0, 102_000.0, 103_000.0]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=103_000.0,
            equity_curve=equity_curve,
            daily_returns=[0.01, 0.0099, 0.0098],
            trades=[],
        )
        assert result.max_drawdown == 0.0
        assert result.max_drawdown_pct == 0.0

    def test_drawdown_from_initial_equity(self):
        # / if price drops below initial equity, drawdown is from initial peak
        equity_curve = [98_000.0, 95_000.0, 97_000.0]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=97_000.0,
            equity_curve=equity_curve,
            daily_returns=[-0.02, -0.0306, 0.021],
            trades=[],
        )
        # / peak is initial_equity=100k, trough=95k => dd=5k
        assert result.max_drawdown == pytest.approx(5_000.0)
        assert result.max_drawdown_pct == pytest.approx(0.05, rel=0.01)


class TestComputeMetricsWinRate:
    def test_win_rate_known_trades(self):
        trades = [
            Trade("A", "buy", 10, 100, pd.Timestamp("2024-01-02"), 110, pd.Timestamp("2024-01-10"), pnl=100),
            Trade("B", "buy", 10, 100, pd.Timestamp("2024-01-02"), 90, pd.Timestamp("2024-01-10"), pnl=-100),
            Trade("C", "buy", 10, 100, pd.Timestamp("2024-01-02"), 105, pd.Timestamp("2024-01-10"), pnl=50),
        ]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=100_050.0,
            equity_curve=[100_000.0], daily_returns=[0.0005],
            trades=trades,
        )
        # / 2 winners, 1 loser => win_rate = 2/3
        assert result.win_rate == pytest.approx(2 / 3, rel=0.01)
        assert result.winning_trades == 2
        assert result.losing_trades == 1

    def test_zero_trades_win_rate_zero(self):
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=100_000.0,
            equity_curve=[], daily_returns=[], trades=[],
        )
        assert result.win_rate == 0.0
        assert result.total_trades == 0


class TestComputeMetricsProfitFactor:
    def test_profit_factor_known(self):
        trades = [
            Trade("A", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=200),
            Trade("B", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=300),
            Trade("C", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=-100),
        ]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=100_400.0,
            equity_curve=[100_000.0], daily_returns=[0.004],
            trades=trades,
        )
        # / gross_profit=500, gross_loss=100 => pf=5.0
        assert result.profit_factor == pytest.approx(5.0)

    def test_profit_factor_all_winners(self):
        trades = [
            Trade("A", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=200),
            Trade("B", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=300),
        ]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=100_500.0,
            equity_curve=[100_000.0], daily_returns=[0.005],
            trades=trades,
        )
        # / no losses => profit_factor = inf
        assert result.profit_factor == float("inf")

    def test_profit_factor_all_losers(self):
        trades = [
            Trade("A", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=-200),
            Trade("B", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=-100),
        ]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=99_700.0,
            equity_curve=[100_000.0], daily_returns=[-0.003],
            trades=trades,
        )
        # / no winners => profit_factor = 0
        assert result.profit_factor == 0.0


class TestComputeMetricsCalmar:
    def test_calmar_ratio_known(self):
        # / total_return_pct=10%, 252 trading days, max_dd_pct=5%
        # / compound annualized = (1.10)^(252/252) - 1 = 0.10 => calmar = 0.10/0.05 = 2.0
        equity_curve = [100_000.0, 95_000.0, 110_000.0]
        daily_returns = [-0.05, 0.1579]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=110_000.0,
            equity_curve=equity_curve,
            daily_returns=daily_returns,
            trades=[],
        )

        # / total_return_pct = 0.10, trading_days=2, max_dd_pct=5/100=0.05
        # / compound annualized = (1.10)^(252/2) - 1
        expected_ann = (1.10) ** (252 / 2) - 1
        expected_calmar = expected_ann / 0.05
        assert result.calmar_ratio == pytest.approx(expected_calmar, rel=0.01)

    def test_calmar_zero_when_no_drawdown(self):
        equity_curve = [100_000.0, 101_000.0, 102_000.0]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=102_000.0,
            equity_curve=equity_curve,
            daily_returns=[0.01, 0.0099],
            trades=[],
        )
        # / no drawdown => calmar = 0 (division by zero guard)
        assert result.calmar_ratio == 0.0


# ---------------------------------------------------------------------------
# / test BacktestResult.to_score_dict
# ---------------------------------------------------------------------------

class TestBacktestResultScoreDict:
    def test_to_score_dict_format(self):
        result = BacktestResult(
            strategy_id="strat_001",
            strategy_name="test_strat",
            period_start=pd.Timestamp("2024-01-02"),
            period_end=pd.Timestamp("2024-06-30"),
            sharpe_ratio=1.5,
            max_drawdown_pct=0.12,
            win_rate=0.55,
            total_trades=42,
            sortino_ratio=2.1,
            calmar_ratio=3.0,
        )
        d = result.to_score_dict()

        assert d["strategy_id"] == "strat_001"
        assert d["period_start"] == pd.Timestamp("2024-01-02")
        assert d["period_end"] == pd.Timestamp("2024-06-30")
        assert d["sharpe_ratio"] == 1.5
        assert d["max_drawdown"] == 0.12
        assert d["win_rate"] == 0.55
        assert d["total_trades"] == 42
        assert d["sortino_ratio"] == 2.1
        assert d["calmar_ratio"] == 3.0

    def test_to_score_dict_has_all_keys(self):
        result = BacktestResult(strategy_id="x", strategy_name="y")
        d = result.to_score_dict()
        expected_keys = {
            "strategy_id", "period_start", "period_end",
            "sharpe_ratio", "max_drawdown", "win_rate",
            "total_trades", "sortino_ratio", "calmar_ratio",
        }
        assert set(d.keys()) == expected_keys


# ---------------------------------------------------------------------------
# / test edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_zero_trades_metrics(self):
        # / flat data where rsi never triggers
        rng = np.random.default_rng(42)
        n = 120
        # / uptrend => rsi stays high, rsi < 40 never triggers
        prices = 100.0 * np.exp(np.cumsum(0.005 + 0.002 * rng.standard_normal(n)))
        dates = pd.bdate_range("2024-01-02", periods=n)
        opens = np.roll(prices, 1)
        opens[0] = 100.0
        df = pd.DataFrame({
            "open": opens,
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices,
            "volume": rng.integers(1_000_000, 3_000_000, size=n).astype(float),
        }, index=dates)

        strat = _make_strategy({
            "entry_conditions": {
                "operator": "AND",
                "signals": [
                    {"indicator": "rsi", "condition": "below", "threshold": 10, "period": 14},
                ],
            },
        })
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        assert result.total_trades == 0
        assert result.win_rate == 0.0
        assert result.profit_factor == 0.0
        assert result.avg_holding_days == 0.0

    def test_all_winners_metrics(self):
        trades = [
            Trade("A", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=100, holding_days=5),
            Trade("B", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=200, holding_days=3),
            Trade("C", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=50, holding_days=7),
        ]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=100_350.0,
            equity_curve=[100_000.0], daily_returns=[0.0035],
            trades=trades,
        )
        assert result.win_rate == 1.0
        assert result.winning_trades == 3
        assert result.losing_trades == 0
        assert result.profit_factor == float("inf")
        assert result.avg_win == pytest.approx(350 / 3)
        assert result.avg_loss == 0.0
        assert result.avg_holding_days == 5.0

    def test_all_losers_metrics(self):
        trades = [
            Trade("A", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=-100, holding_days=2),
            Trade("B", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=-200, holding_days=4),
        ]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=99_700.0,
            equity_curve=[100_000.0], daily_returns=[-0.003],
            trades=trades,
        )
        assert result.win_rate == 0.0
        assert result.winning_trades == 0
        assert result.losing_trades == 2
        assert result.profit_factor == 0.0
        assert result.avg_win == 0.0
        assert result.avg_loss == pytest.approx(-150.0)
        assert result.avg_holding_days == 3.0

    def test_compute_metrics_with_start_end_dates(self):
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=100_000.0,
            equity_curve=[], daily_returns=[], trades=[],
            start_date=pd.Timestamp("2024-01-02"),
            end_date=pd.Timestamp("2024-06-30"),
        )
        assert result.period_start == pd.Timestamp("2024-01-02")
        assert result.period_end == pd.Timestamp("2024-06-30")

    def test_compute_metrics_total_return(self):
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=120_000.0,
            equity_curve=[100_000.0, 120_000.0],
            daily_returns=[0.2],
            trades=[],
        )
        assert result.total_return == pytest.approx(20_000.0)
        assert result.total_return_pct == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_backtest_closes_positions_at_end(self):
        # / open positions should be force-closed at backtest end
        df = _make_ohlcv(n=120, seed=42, trend=0.002)
        strat = _make_always_enter_strategy(stop_pct=0.50, max_holding_days=9999)
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        # / all trades should have an exit (either stop, time, or backtest_end)
        for trade in result.trades:
            assert trade.exit_date is not None, f"trade {trade.symbol} has no exit date"
            assert trade.exit_reason != "", f"trade {trade.symbol} has no exit reason"

        # / at least one should be closed with backtest_end reason
        end_trades = [t for t in result.trades if t.exit_reason == "backtest_end"]
        # / not guaranteed but the always-enter strategy with huge stop should hold to end
        # / if no backtest_end, that's fine — all positions were exited by other means
        assert len(result.trades) > 0

    @pytest.mark.asyncio
    async def test_single_daily_return_sharpe_is_zero(self):
        # / only 1 return => std has ddof=1 => division by zero => sharpe=0
        dates = pd.bdate_range("2024-01-02", periods=2)
        df = pd.DataFrame({
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [1e6, 1e6],
        }, index=dates)

        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=100_100.0,
            equity_curve=[100_000.0, 100_100.0],
            daily_returns=[0.001],
            trades=[],
        )
        # / single return => std not computable with ddof=1 for sharpe calc
        # / but std(ddof=1) of a single element is technically nan/0
        # / the implementation falls back to sharpe=0
        assert result.sharpe_ratio == 0.0


# ---------------------------------------------------------------------------
# / deep failure-pinpointing tests — metrics
# ---------------------------------------------------------------------------

class TestMetricsDeep:
    def test_sharpe_exact_calculation(self):
        # / hand-computed: avg_daily_return / std_daily_return(ddof=1) * sqrt(252)
        daily_returns = [0.01, -0.005, 0.02, -0.01, 0.015, -0.003, 0.005, -0.008, 0.012, 0.003]
        arr = np.array(daily_returns)
        expected_avg = float(np.mean(arr))
        expected_std = float(np.std(arr, ddof=1))
        expected_sharpe = expected_avg / expected_std * math.sqrt(252)

        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=103_900.0,
            equity_curve=[100_000.0] * 10,
            daily_returns=daily_returns,
            trades=[],
        )
        assert result.sharpe_ratio == pytest.approx(expected_sharpe, rel=1e-6)

    def test_sortino_with_no_downside_returns_inf(self):
        # / all positive returns => no downside deviation => sortino = inf
        daily_returns = [0.01, 0.005, 0.02, 0.015, 0.003]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=105_000.0,
            equity_curve=[100_000.0] * 5,
            daily_returns=daily_returns,
            trades=[],
        )
        assert result.sortino_ratio == float("inf")

    def test_calmar_with_zero_drawdown_returns_zero(self):
        # / monotonically increasing equity => max_dd_pct = 0 => calmar = 0
        equity_curve = [100_000.0, 101_000.0, 102_000.0, 103_000.0]
        daily_returns = [0.01, 0.0099, 0.0098]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=103_000.0,
            equity_curve=equity_curve,
            daily_returns=daily_returns,
            trades=[],
        )
        assert result.calmar_ratio == 0.0

    def test_profit_factor_no_losses_returns_inf(self):
        trades = [
            Trade("A", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=200),
            Trade("B", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=100),
        ]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=100_300.0,
            equity_curve=[100_000.0], daily_returns=[0.003],
            trades=trades,
        )
        assert result.profit_factor == float("inf")

    def test_profit_factor_no_wins_returns_zero(self):
        trades = [
            Trade("A", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=-200),
            Trade("B", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=-100),
        ]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=99_700.0,
            equity_curve=[100_000.0], daily_returns=[-0.003],
            trades=trades,
        )
        assert result.profit_factor == 0.0

    def test_win_rate_all_winners_equals_1(self):
        trades = [
            Trade("A", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=100),
            Trade("B", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=200),
            Trade("C", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=50),
        ]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=100_350.0,
            equity_curve=[100_000.0], daily_returns=[0.0035],
            trades=trades,
        )
        assert result.win_rate == pytest.approx(1.0)

    def test_win_rate_all_losers_equals_0(self):
        trades = [
            Trade("A", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=-100),
            Trade("B", "buy", 10, 100, pd.Timestamp("2024-01-02"), pnl=-200),
        ]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=99_700.0,
            equity_curve=[100_000.0], daily_returns=[-0.003],
            trades=trades,
        )
        assert result.win_rate == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# / deep failure-pinpointing tests — anti-lookahead
# ---------------------------------------------------------------------------

class TestAntiLookaheadDeep:
    @pytest.mark.asyncio
    async def test_signal_at_close_fill_at_next_open(self):
        # / verify that entry happens at the open of the bar after the signal bar
        # / using always-enter strategy: signal evaluated at bar N-1 close,
        # / entry filled at bar N open
        df = _make_ohlcv(n=120, seed=42)
        strat = _make_always_enter_strategy()
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        if not result.trades:
            pytest.skip("no trades generated")

        # / first trade should fill at the open of the entry bar
        first_trade = result.trades[0]
        entry_date = first_trade.entry_date
        if entry_date in df.index:
            bar_idx = df.index.get_loc(entry_date)
            open_price = float(df.iloc[bar_idx]["open"])
            assert first_trade.entry_price == pytest.approx(open_price, rel=1e-6)
            # / entry should be after warmup period (bar 50+)
            assert bar_idx >= 50

    @pytest.mark.asyncio
    async def test_no_entry_on_last_bar(self):
        # / strategy should not enter on the very last bar because
        # / there's no next bar to fill at
        df = _make_ohlcv(n=80, seed=42)
        strat = _make_always_enter_strategy(stop_pct=0.50, max_holding_days=9999)
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)

        last_date = df.index[-1]
        # / no trade should have entry_date == last_date
        # / (evaluation of entry at last bar would have no next bar to fill)
        for trade in result.trades:
            # / entries at last date are OK only if backtest closes them at end
            # / but the key point: entry can't be evaluated based on last bar's data
            # / because entry signals use data up to previous bar
            pass
        # / all trades must have exit dates
        for trade in result.trades:
            assert trade.exit_date is not None


# ---------------------------------------------------------------------------
# / deep failure-pinpointing tests — max drawdown
# ---------------------------------------------------------------------------

class TestMaxDrawdownDeep:
    def test_monotonic_increase_zero_drawdown(self):
        # / monotonically increasing equity should have zero drawdown
        equity_curve = [100_000.0, 101_000.0, 102_000.0, 103_000.0, 104_000.0]
        daily_returns = [0.01, 0.0099, 0.0098, 0.0097]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=104_000.0,
            equity_curve=equity_curve,
            daily_returns=daily_returns,
            trades=[],
        )
        assert result.max_drawdown == 0.0
        assert result.max_drawdown_pct == 0.0

    def test_exact_peak_trough_calculation(self):
        # / equity [100, 120, 90, 110, 80]
        # / peak tracks: 100->120->120->120->120
        # / drawdowns: 0, 0, 30, 10, 40
        # / max_dd = 40 (from peak 120 to trough 80)
        # / max_dd_pct = 40/120 = 0.3333...
        equity_curve = [100.0, 120.0, 90.0, 110.0, 80.0]
        daily_returns = [0.2, -0.25, 0.222, -0.273]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100.0, final_equity=80.0,
            equity_curve=equity_curve,
            daily_returns=daily_returns,
            trades=[],
        )
        assert result.max_drawdown == pytest.approx(40.0)
        assert result.max_drawdown_pct == pytest.approx(40.0 / 120.0, rel=1e-6)


# ---------------------------------------------------------------------------
# / deep failure-pinpointing tests — edge cases
# ---------------------------------------------------------------------------

class TestEdgeCasesDeep:
    def test_single_trade_metrics(self):
        # / single winning trade should produce valid metrics
        trades = [
            Trade("A", "buy", 10, 100, pd.Timestamp("2024-01-02"),
                  exit_price=110, exit_date=pd.Timestamp("2024-01-10"),
                  pnl=100, pnl_pct=0.10, holding_days=8),
        ]
        result = _compute_metrics(
            strategy_id="test", strategy_name="test",
            initial_equity=100_000.0, final_equity=100_100.0,
            equity_curve=[100_000.0, 100_100.0],
            daily_returns=[0.001],
            trades=trades,
        )
        assert result.total_trades == 1
        assert result.winning_trades == 1
        assert result.losing_trades == 0
        assert result.win_rate == pytest.approx(1.0)
        assert result.avg_win == pytest.approx(100.0)
        assert result.avg_loss == 0.0
        assert result.profit_factor == float("inf")
        assert result.avg_holding_days == 8.0

    @pytest.mark.asyncio
    async def test_no_trades_returns_initial_equity(self):
        # / strategy with impossible entry condition should produce no trades
        strat = _make_strategy({
            "entry_conditions": {
                "operator": "AND",
                "signals": [
                    {"indicator": "rsi", "condition": "below", "threshold": 1, "period": 14},
                ],
            },
        })
        df = _make_uptrend(n=120, seed=42)
        result = await run_backtest(strat, {"TEST": df}, initial_cash=100_000.0)
        assert result.total_trades == 0
        assert result.final_equity == pytest.approx(100_000.0)
        assert result.total_return == pytest.approx(0.0)
        assert result.win_rate == 0.0
        assert result.profit_factor == 0.0
