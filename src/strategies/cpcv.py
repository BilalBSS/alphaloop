# / combinatorial purged cross-validation — marcos lopez de prado method
# / estimates probability of backtest overfitting (PBO)

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd
import structlog

from .backtest import BacktestResult, run_backtest
from .base_strategy import ConfigDrivenStrategy

logger = structlog.get_logger(__name__)


@dataclass
class CPCVResult:
    strategy_id: str
    num_paths: int = 0
    pbo: float = 0.0
    avg_oos_sharpe: float = 0.0
    is_overfit: bool = False


async def run_cpcv(
    strategy: ConfigDrivenStrategy,
    market_data: dict[str, pd.DataFrame],
    num_groups: int = 6,
    num_test_groups: int = 2,
) -> CPCVResult:
    # / split data into groups, test all train/test combinations
    result = CPCVResult(strategy_id=strategy.strategy_id)

    all_dates = set()
    for df in market_data.values():
        all_dates.update(df.index)
    sorted_dates = sorted(all_dates)
    total = len(sorted_dates)
    group_size = total // num_groups

    if group_size < 30:
        logger.warning("cpcv_insufficient_data", bars=total, groups=num_groups)
        return result

    # / create date groups
    groups = []
    for i in range(num_groups):
        start = i * group_size
        end = start + group_size if i < num_groups - 1 else total
        groups.append((sorted_dates[start], sorted_dates[end - 1]))

    test_combos = list(combinations(range(num_groups), num_test_groups))
    result.num_paths = len(test_combos)

    oos_sharpes = []
    for test_indices in test_combos:
        # / collect test dates
        test_dates = set()
        for idx in test_indices:
            start_d, end_d = groups[idx]
            for d in sorted_dates:
                if start_d <= d <= end_d:
                    test_dates.add(d)

        # / slice market data for test period
        oos_data = {}
        for sym, df in market_data.items():
            mask = df.index.isin(test_dates)
            sliced = df[mask]
            if len(sliced) >= 10:
                oos_data[sym] = sliced

        if not oos_data:
            continue

        bt = await run_backtest(strategy, oos_data)
        oos_sharpes.append(bt.sharpe_ratio)

    if oos_sharpes:
        result.avg_oos_sharpe = float(np.mean(oos_sharpes))
        # / PBO = fraction of paths where OOS sharpe <= 0
        result.pbo = sum(1 for s in oos_sharpes if s <= 0) / len(oos_sharpes)
        result.is_overfit = result.pbo > 0.5

    return result
