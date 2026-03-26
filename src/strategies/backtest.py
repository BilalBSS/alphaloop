# / backtesting engine — simulates strategy against historical data
# / correctness is critical: no lookahead bias, realistic fills via paper broker
# / the evolution engine will optimize for whatever this rewards, including bugs
#
# / anti-lookahead rules:
# /   - strategy only sees data up to and including current bar
# /   - entry signals evaluated at bar close, filled at next bar open
# /   - exit signals evaluated at bar close, filled at next bar open
# /   - no future prices, volumes, or indicators leak into decisions

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import structlog

from src.brokers.paper_broker import PaperBroker

from .base_strategy import AnalysisData, ConfigDrivenStrategy

logger = structlog.get_logger(__name__)


@dataclass
class Trade:
    symbol: str
    side: str
    qty: float
    entry_price: float
    entry_date: pd.Timestamp
    exit_price: float | None = None
    exit_date: pd.Timestamp | None = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""
    holding_days: int = 0


@dataclass
class BacktestResult:
    strategy_id: str
    strategy_name: str
    period_start: pd.Timestamp | None = None
    period_end: pd.Timestamp | None = None
    initial_equity: float = 100_000.0
    final_equity: float = 100_000.0
    total_return: float = 0.0
    total_return_pct: float = 0.0

    # / core metrics
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0

    # / trade stats
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    avg_holding_days: float = 0.0

    # / equity curve
    equity_curve: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    daily_returns: list[float] = field(default_factory=list)

    def to_score_dict(self) -> dict[str, Any]:
        # / format for strategy_scores table
        return {
            "strategy_id": self.strategy_id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown_pct,
            "win_rate": self.win_rate,
            "total_trades": self.total_trades,
            "sortino_ratio": self.sortino_ratio,
            "calmar_ratio": self.calmar_ratio,
        }


@dataclass
class OpenPosition:
    symbol: str
    qty: float
    entry_price: float
    entry_date: pd.Timestamp
    entry_bar_idx: int


async def run_backtest(
    strategy: ConfigDrivenStrategy,
    market_data: dict[str, pd.DataFrame],
    analysis_data: dict[str, AnalysisData] | None = None,
    initial_cash: float = 100_000.0,
    max_open_positions: int = 10,
) -> BacktestResult:
    # / run a full backtest of a strategy against historical data
    # / market_data: dict of symbol -> ohlcv dataframe (index=datetime, cols=open,high,low,close,volume)
    # / analysis_data: optional dict of symbol -> AnalysisData for fundamental checks
    #
    # / flow per bar:
    # /   1. update broker prices to current bar
    # /   2. check exit signals for open positions (uses data up to current bar)
    # /   3. execute pending exits at current bar open (filled at open price)
    # /   4. check entry signals for universe (uses data up to PREVIOUS bar)
    # /   5. execute entries at current bar open

    broker = PaperBroker(initial_cash=initial_cash)
    universe = list(market_data.keys())

    if not universe:
        return BacktestResult(
            strategy_id=strategy.strategy_id,
            strategy_name=strategy.name,
            initial_equity=initial_cash,
            final_equity=initial_cash,
        )

    # / find the common date range across all symbols
    all_dates: set[Any] = set()
    for df in market_data.values():
        all_dates.update(df.index.tolist())
    sorted_dates = sorted(all_dates)

    if len(sorted_dates) < 2:
        return BacktestResult(
            strategy_id=strategy.strategy_id,
            strategy_name=strategy.name,
            initial_equity=initial_cash,
            final_equity=initial_cash,
        )

    open_positions: dict[str, OpenPosition] = {}
    closed_trades: list[Trade] = []
    equity_curve: list[float] = []
    daily_returns: list[float] = []
    prev_equity = initial_cash

    # / minimum bars before we start trading (enough for indicators)
    warmup_bars = 50

    for date_idx, current_date in enumerate(sorted_dates):
        # / 1. update broker prices to current bar
        for symbol, df in market_data.items():
            if current_date in df.index:
                bar = df.loc[current_date]
                # / use open price for fills (realistic: signal at close, fill at next open)
                broker.set_price(symbol, float(bar["open"]) if date_idx > 0 else float(bar["close"]))

        # / 2. check exits for open positions
        exits_to_process: list[tuple[str, str]] = []  # (symbol, reason)
        for symbol, pos in list(open_positions.items()):
            if symbol not in market_data or current_date not in market_data[symbol].index:
                continue
            df = market_data[symbol]
            bar_idx = df.index.get_loc(current_date)

            # / only check exit if we have enough data
            if bar_idx < 1:
                continue

            # / evaluate exit using data up to previous bar (decision made at close of prev bar)
            exit_signal = strategy.should_exit(
                symbol=symbol,
                market_data=df.iloc[:bar_idx],  # / data up to but not including current bar
                entry_price=pos.entry_price,
                entry_date=pos.entry_date,
                current_bar_idx=bar_idx - 1,  # / evaluate at previous bar close
            )
            if exit_signal.should_exit:
                exits_to_process.append((symbol, exit_signal.reason))

        # / 3. execute exits at current bar open
        for symbol, reason in exits_to_process:
            pos = open_positions.get(symbol)
            if pos is None:
                continue

            # / set price to current open for fill
            if current_date in market_data[symbol].index:
                fill_price = float(market_data[symbol].loc[current_date, "open"])
                broker.set_price(symbol, fill_price)

                order = await broker.place_order(symbol, pos.qty, "sell")
                if order.status == "filled":
                    pnl = (fill_price - pos.entry_price) * pos.qty
                    pnl_pct = (fill_price - pos.entry_price) / pos.entry_price
                    holding = (current_date - pos.entry_date).days if hasattr(current_date - pos.entry_date, "days") else 0
                    closed_trades.append(Trade(
                        symbol=symbol, side="buy", qty=pos.qty,
                        entry_price=pos.entry_price, entry_date=pos.entry_date,
                        exit_price=fill_price, exit_date=current_date,
                        pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason=reason, holding_days=holding,
                    ))
                    del open_positions[symbol]

        # / skip entry evaluation during warmup
        if date_idx < warmup_bars:
            # / still track equity
            balance = await broker.get_account_balance()
            equity_curve.append(balance.equity)
            if prev_equity > 0:
                daily_returns.append((balance.equity - prev_equity) / prev_equity)
            prev_equity = balance.equity
            continue

        # / 4. check entries (evaluate using data up to previous bar close)
        entries_to_process: list[tuple[str, float, float]] = []  # (symbol, strength, open_price)
        if len(open_positions) < max_open_positions:
            for symbol in universe:
                if symbol in open_positions:
                    continue
                if current_date not in market_data[symbol].index:
                    continue

                df = market_data[symbol]
                bar_idx = df.index.get_loc(current_date)
                if bar_idx < warmup_bars:
                    continue

                # / evaluate entry using data up to previous bar (no lookahead)
                data_for_eval = df.iloc[:bar_idx]  # / everything before current bar

                analysis = analysis_data.get(symbol) if analysis_data else None
                entry_signal = strategy.should_enter(symbol, data_for_eval, analysis)

                if entry_signal.should_enter:
                    open_price = float(df.loc[current_date, "open"])
                    entries_to_process.append((symbol, entry_signal.strength, open_price))

        # / 5. execute entries at current bar open (sorted by strength, strongest first)
        entries_to_process.sort(key=lambda x: x[1], reverse=True)
        balance = await broker.get_account_balance()

        for symbol, strength, open_price in entries_to_process:
            if len(open_positions) >= max_open_positions:
                break

            broker.set_price(symbol, open_price)
            sizing = strategy.position_size(balance.equity, open_price, strength)
            if sizing.qty <= 0:
                continue

            order = await broker.place_order(symbol, sizing.qty, "buy")
            if order.status == "filled":
                open_positions[symbol] = OpenPosition(
                    symbol=symbol,
                    qty=sizing.qty,
                    entry_price=order.filled_price,
                    entry_date=current_date,
                    entry_bar_idx=date_idx,
                )
                # / refresh balance after each entry
                balance = await broker.get_account_balance()

        # / update prices to close for equity tracking
        for symbol, df in market_data.items():
            if current_date in df.index:
                broker.set_price(symbol, float(df.loc[current_date, "close"]))

        balance = await broker.get_account_balance()
        equity_curve.append(balance.equity)
        if prev_equity > 0:
            daily_returns.append((balance.equity - prev_equity) / prev_equity)
        prev_equity = balance.equity

    # / close any remaining open positions at last bar close
    last_date = sorted_dates[-1]
    for symbol, pos in list(open_positions.items()):
        if symbol in market_data and last_date in market_data[symbol].index:
            close_price = float(market_data[symbol].loc[last_date, "close"])
            broker.set_price(symbol, close_price)
            order = await broker.place_order(symbol, pos.qty, "sell")
            if order.status == "filled":
                pnl = (close_price - pos.entry_price) * pos.qty
                pnl_pct = (close_price - pos.entry_price) / pos.entry_price
                holding = (last_date - pos.entry_date).days if hasattr(last_date - pos.entry_date, "days") else 0
                closed_trades.append(Trade(
                    symbol=symbol, side="buy", qty=pos.qty,
                    entry_price=pos.entry_price, entry_date=pos.entry_date,
                    exit_price=close_price, exit_date=last_date,
                    pnl=pnl, pnl_pct=pnl_pct,
                    exit_reason="backtest_end", holding_days=holding,
                ))

    # / compute final metrics
    final_balance = await broker.get_account_balance()
    result = _compute_metrics(
        strategy_id=strategy.strategy_id,
        strategy_name=strategy.name,
        initial_equity=initial_cash,
        final_equity=final_balance.equity,
        equity_curve=equity_curve,
        daily_returns=daily_returns,
        trades=closed_trades,
        start_date=sorted_dates[0] if sorted_dates else None,
        end_date=sorted_dates[-1] if sorted_dates else None,
    )
    return result


def _compute_metrics(
    strategy_id: str,
    strategy_name: str,
    initial_equity: float,
    final_equity: float,
    equity_curve: list[float],
    daily_returns: list[float],
    trades: list[Trade],
    start_date: Any = None,
    end_date: Any = None,
) -> BacktestResult:
    total_return = final_equity - initial_equity
    total_return_pct = total_return / initial_equity if initial_equity > 0 else 0

    # / drawdown calculation from equity curve
    max_dd = 0.0
    max_dd_pct = 0.0
    peak = initial_equity
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        dd_pct = dd / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    # / sharpe ratio (annualized, assuming ~252 trading days)
    returns_arr = np.array(daily_returns) if daily_returns else np.array([0.0])
    avg_return = float(np.mean(returns_arr))
    std_return = float(np.std(returns_arr, ddof=1)) if len(returns_arr) > 1 else 0.0
    sharpe = (avg_return / std_return * math.sqrt(252)) if std_return > 0 else 0.0

    # / sortino ratio (downside deviation only)
    downside = returns_arr[returns_arr < 0]
    downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0
    sortino = (avg_return / downside_std * math.sqrt(252)) if downside_std > 0 else 0.0

    # / calmar ratio (annualized return / max drawdown)
    trading_days = len(daily_returns)
    if trading_days > 0 and max_dd_pct > 0:
        annualized_return = total_return_pct * (252 / trading_days)
        calmar = annualized_return / max_dd_pct
    else:
        calmar = 0.0

    # / trade stats
    total_trades = len(trades)
    winners = [t for t in trades if t.pnl > 0]
    losers = [t for t in trades if t.pnl <= 0]
    win_rate = len(winners) / total_trades if total_trades > 0 else 0.0
    avg_win = sum(t.pnl for t in winners) / len(winners) if winners else 0.0
    avg_loss = sum(t.pnl for t in losers) / len(losers) if losers else 0.0
    gross_profit = sum(t.pnl for t in winners) if winners else 0.0
    gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
    avg_holding = sum(t.holding_days for t in trades) / total_trades if total_trades > 0 else 0.0

    return BacktestResult(
        strategy_id=strategy_id,
        strategy_name=strategy_name,
        period_start=pd.Timestamp(start_date) if start_date is not None else None,
        period_end=pd.Timestamp(end_date) if end_date is not None else None,
        initial_equity=initial_equity,
        final_equity=final_equity,
        total_return=total_return,
        total_return_pct=total_return_pct,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        total_trades=total_trades,
        winning_trades=len(winners),
        losing_trades=len(losers),
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        avg_holding_days=avg_holding,
        equity_curve=equity_curve,
        trades=trades,
        daily_returns=daily_returns,
    )
