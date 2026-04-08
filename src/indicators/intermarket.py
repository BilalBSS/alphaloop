# / intermarket signals from bonds, dollar, credit, gold
# / tracks cross-asset regime (risk-on vs risk-off)

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

INTERMARKET_SYMBOLS = ["TLT", "UUP", "HYG", "GLD"]


@dataclass
class IntermarketSignals:
    bond_equity_divergence: float
    dollar_strength: float
    credit_stress: float
    gold_signal: float
    composite: float


def compute_intermarket(
    spy: pd.DataFrame,
    tlt: pd.DataFrame | None = None,
    uup: pd.DataFrame | None = None,
    hyg: pd.DataFrame | None = None,
    gld: pd.DataFrame | None = None,
    window: int = 20,
) -> IntermarketSignals:
    # / compute intermarket regime signals

    def _momentum(df: pd.DataFrame | None, w: int) -> float:
        if df is None or len(df) < w + 1:
            return 0.0
        ret = (df["close"].iloc[-1] / df["close"].iloc[-w] - 1)
        return float(np.clip(ret * 10, -1.0, 1.0))

    spy_mom = _momentum(spy, window)
    tlt_mom = _momentum(tlt, window)
    uup_mom = _momentum(uup, window)
    hyg_mom = _momentum(hyg, window)
    gld_mom = _momentum(gld, window)

    # / bond/equity divergence: TLT up + SPY down = risk-off
    bond_eq_div = float(np.clip(tlt_mom - spy_mom, -1.0, 1.0)) if tlt is not None else 0.0

    # / credit stress: HYG declining relative to TLT
    credit = float(np.clip(tlt_mom - hyg_mom, -1.0, 1.0)) if (tlt is not None and hyg is not None) else 0.0

    # / composite: negative = risk-off, positive = risk-on
    composite = -0.3 * bond_eq_div - 0.2 * uup_mom - 0.3 * credit - 0.2 * gld_mom
    composite = float(np.clip(composite, -1.0, 1.0))

    return IntermarketSignals(
        bond_equity_divergence=bond_eq_div,
        dollar_strength=uup_mom,
        credit_stress=credit,
        gold_signal=gld_mom,
        composite=composite,
    )
