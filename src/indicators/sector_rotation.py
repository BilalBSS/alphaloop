
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class SectorStrength:
    sector: str
    rs_20d: float
    rs_60d: float
    rank: int
    momentum: str


def compute_sector_rotation(
    sector_data: dict[str, pd.DataFrame],
    spy_data: pd.DataFrame,
) -> list[SectorStrength]:
    results = []

    for sector, df in sector_data.items():
        if len(df) < 61 or len(spy_data) < 61:
            continue

        rs_20 = _relative_strength(df, spy_data, 20)
        rs_60 = _relative_strength(df, spy_data, 60)

        if rs_20 > rs_60:
            momentum = "accelerating"
        elif rs_20 < rs_60 * 0.9:
            momentum = "decelerating"
        else:
            momentum = "stable"

        results.append(SectorStrength(
            sector=sector, rs_20d=rs_20, rs_60d=rs_60, rank=0, momentum=momentum,
        ))

    results.sort(key=lambda x: x.rs_20d, reverse=True)
    for i, r in enumerate(results):
        r.rank = i + 1

    return results


def _relative_strength(sector_df: pd.DataFrame, spy_df: pd.DataFrame, period: int) -> float:
    if len(sector_df) < period + 1 or len(spy_df) < period + 1:
        return 0.0
    sec_ret = sector_df["close"].iloc[-1] / sector_df["close"].iloc[-period] - 1
    spy_ret = spy_df["close"].iloc[-1] / spy_df["close"].iloc[-period] - 1
    if spy_ret == 0:
        return 0.0
    return float(sec_ret / abs(spy_ret))
