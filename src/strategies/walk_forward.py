
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import structlog

from .backtest import BacktestResult, run_backtest
from .base_strategy import ConfigDrivenStrategy

logger = structlog.get_logger(__name__)


@dataclass
class WalkForwardResult:
    strategy_id: str
    num_windows: int = 0
    oos_results: list[BacktestResult] = field(default_factory=list)
    avg_oos_sharpe: float = 0.0
    avg_oos_return: float = 0.0
    is_degradation: bool = False


async def walk_forward_test(
    strategy: ConfigDrivenStrategy,
    market_data: dict[str, pd.DataFrame],
    num_windows: int = 5,
    train_pct: float = 0.7,
) -> WalkForwardResult:
    result = WalkForwardResult(strategy_id=strategy.strategy_id, num_windows=num_windows)

    all_dates = set()
    for df in market_data.values():
        all_dates.update(df.index)
    if not all_dates:
        return result
    sorted_dates = sorted(all_dates)
    total_bars = len(sorted_dates)
    window_size = total_bars // num_windows

    if window_size < 50:
        logger.warning("walk_forward_insufficient_data", bars=total_bars, windows=num_windows)
        return result

    for i in range(num_windows):
        start = i * window_size
        end = min(start + window_size, total_bars)
        train_end = start + int((end - start) * train_pct)

        oos_start_date = sorted_dates[train_end]
        oos_end_date = sorted_dates[end - 1] if end > 0 else sorted_dates[-1]

        oos_data = {}
        for sym, df in market_data.items():
            mask = (df.index >= oos_start_date) & (df.index <= oos_end_date)
            sliced = df[mask]
            if len(sliced) >= 10:
                oos_data[sym] = sliced

        if not oos_data:
            continue

        oos_bt = await run_backtest(strategy, oos_data)
        result.oos_results.append(oos_bt)

    if result.oos_results:
        result.avg_oos_sharpe = sum(r.sharpe_ratio for r in result.oos_results) / len(result.oos_results)
        result.avg_oos_return = sum(r.total_return_pct for r in result.oos_results) / len(result.oos_results)

    return result
