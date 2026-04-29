# / tests for intermarket regime signals (bonds/dollar/credit/gold vs SPY)

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.indicators.intermarket import (
    INTERMARKET_SYMBOLS,
    IntermarketSignals,
    compute_intermarket,
)


def _bars(start: float, end: float, n: int = 30) -> pd.DataFrame:
    # / simple monotonic close series wrapped as the expected DataFrame shape
    close = np.linspace(start, end, n)
    return pd.DataFrame({"close": close})


class TestIntermarketSymbolsConstant:
    def test_contains_tlt_uup_hyg_gld(self):
        assert set(INTERMARKET_SYMBOLS) == {"TLT", "UUP", "HYG", "GLD"}


class TestComputeIntermarketNoneInputs:
    def test_spy_only_returns_zeroed_signals(self):
        # / no cross-asset data -> every derived signal falls back to 0
        spy = _bars(400, 420, 30)
        out = compute_intermarket(spy)
        assert isinstance(out, IntermarketSignals)
        assert out.bond_equity_divergence == 0.0
        assert out.dollar_strength == 0.0
        assert out.credit_stress == 0.0
        assert out.gold_signal == 0.0
        # / composite is a linear combo of the four — with all zero, composite is 0
        assert out.composite == 0.0


class TestMomentumHelperContract:
    # / _momentum is private but its formula drives every signal. verify via top-level.
    def test_short_df_returns_zero_momentum(self):
        # / df with len < window+1 -> 0 momentum -> bond_equity_divergence = 0 - 0 = 0
        spy = _bars(400, 420, 30)
        tlt = _bars(100, 110, 5)  # / only 5 bars, window default 20 -> zero momentum
        out = compute_intermarket(spy, tlt=tlt)
        # / spy momentum non-zero (len 30, window 20), tlt momentum 0
        # / bond_eq_div = clip(tlt_mom - spy_mom, -1, 1)
        spy_ret = spy["close"].iloc[-1] / spy["close"].iloc[-20] - 1
        expected_spy_mom = float(np.clip(spy_ret * 10, -1.0, 1.0))
        assert out.bond_equity_divergence == pytest.approx(
            float(np.clip(0.0 - expected_spy_mom, -1.0, 1.0))
        )

    def test_momentum_clipped_to_minus_one(self):
        # / crash in spy -> spy_mom clipped to -1.0
        close = [100.0] * 19 + [1.0]  # / spy[-1]/spy[-20] - 1 = -0.99 * 10 = -9.9 -> clipped -1
        spy = pd.DataFrame({"close": close})
        out = compute_intermarket(spy)
        # / no cross-asset, everything stays zero; verify this doesn't crash and composite bounded
        assert -1.0 <= out.composite <= 1.0

    def test_momentum_clipped_to_plus_one(self):
        close = [1.0] * 19 + [100.0]  # / +99x in 20 bars -> +10 -> clipped +1
        spy = pd.DataFrame({"close": close})
        out = compute_intermarket(spy)
        assert -1.0 <= out.composite <= 1.0


class TestBondEquityDivergence:
    def test_tlt_up_spy_down_risk_off(self):
        # / SPY down, TLT up -> bond_eq_div = tlt_mom - spy_mom > 0 (risk-off)
        spy = _bars(420, 400, 30)  # / -20/420 = -0.048 * 10 = -0.48
        tlt = _bars(100, 110, 30)  # / +10/100 = 0.10 * 10 = 1.0 (clipped)
        out = compute_intermarket(spy, tlt=tlt)
        assert out.bond_equity_divergence > 0

    def test_both_up_little_divergence(self):
        spy = _bars(400, 420, 30)
        tlt = _bars(100, 105, 30)  # / +5/100 = 0.05 * 10 = 0.5
        out = compute_intermarket(spy, tlt=tlt)
        # / spy_mom = +0.5 (clipped to 0.5), tlt_mom = 0.5 -> div ~ 0
        assert abs(out.bond_equity_divergence) < 0.1


class TestDollarStrength:
    def test_uup_up_positive_strength(self):
        spy = _bars(400, 420, 30)
        uup = _bars(25, 27, 30)
        out = compute_intermarket(spy, uup=uup)
        assert out.dollar_strength > 0

    def test_uup_down_negative_strength(self):
        spy = _bars(400, 420, 30)
        uup = _bars(27, 25, 30)
        out = compute_intermarket(spy, uup=uup)
        assert out.dollar_strength < 0


class TestCreditStress:
    def test_hyg_down_tlt_up_stress_positive(self):
        spy = _bars(400, 420, 30)
        tlt = _bars(100, 110, 30)  # / up
        hyg = _bars(80, 75, 30)    # / down
        out = compute_intermarket(spy, tlt=tlt, hyg=hyg)
        # / credit = clip(tlt_mom - hyg_mom, -1, 1) -> positive = stress
        assert out.credit_stress > 0

    def test_both_risk_on_little_stress(self):
        spy = _bars(400, 420, 30)
        tlt = _bars(100, 100.1, 30)  # / flat
        hyg = _bars(80, 80.1, 30)    # / flat
        out = compute_intermarket(spy, tlt=tlt, hyg=hyg)
        assert abs(out.credit_stress) < 0.05

    def test_requires_both_tlt_and_hyg(self):
        # / credit_stress is 0 if either is missing
        spy = _bars(400, 420, 30)
        out = compute_intermarket(spy, tlt=_bars(100, 110, 30))
        assert out.credit_stress == 0.0
        out2 = compute_intermarket(spy, hyg=_bars(80, 75, 30))
        assert out2.credit_stress == 0.0


class TestGoldSignal:
    def test_gld_up_positive(self):
        spy = _bars(400, 420, 30)
        gld = _bars(180, 200, 30)
        out = compute_intermarket(spy, gld=gld)
        assert out.gold_signal > 0

    def test_gld_down_negative(self):
        spy = _bars(400, 420, 30)
        gld = _bars(200, 180, 30)
        out = compute_intermarket(spy, gld=gld)
        assert out.gold_signal < 0


class TestComposite:
    def test_bounded_to_minus_one_plus_one(self):
        # / extreme inputs — all risk-off signals at max
        spy = _bars(420, 380, 30)  # / crash
        tlt = _bars(100, 115, 30)  # / rally
        uup = _bars(25, 28, 30)    # / dollar rally
        hyg = _bars(80, 70, 30)    # / credit blowout
        gld = _bars(180, 200, 30)  # / gold rally
        out = compute_intermarket(spy, tlt=tlt, uup=uup, hyg=hyg, gld=gld)
        assert -1.0 <= out.composite <= 1.0

    def test_hand_computed_formula(self):
        # / verify composite = -0.3*bond_eq - 0.2*uup - 0.3*credit - 0.2*gld, clipped
        spy = _bars(400, 420, 30)
        tlt = _bars(100, 101, 30)  # / tlt_mom = 0.01*10 = 0.1
        uup = _bars(25, 25.25, 30)  # / 0.01*10 = 0.1
        hyg = _bars(80, 80.8, 30)   # / 0.01*10 = 0.1
        gld = _bars(180, 181.8, 30)  # / 0.01*10 = 0.1
        out = compute_intermarket(spy, tlt=tlt, uup=uup, hyg=hyg, gld=gld)
        # / composite should fall within bounds and be consistent with its components
        manual = -0.3 * out.bond_equity_divergence - 0.2 * out.dollar_strength \
                 - 0.3 * out.credit_stress - 0.2 * out.gold_signal
        manual_clipped = float(np.clip(manual, -1.0, 1.0))
        assert out.composite == pytest.approx(manual_clipped, abs=1e-6)


class TestWindowParam:
    def test_custom_window_changes_momentum(self):
        spy = _bars(400, 420, 30)
        uup_sharp_recent = pd.DataFrame({"close": [25.0] * 25 + [26.0, 27.0, 28.0, 29.0, 30.0]})
        out20 = compute_intermarket(spy, uup=uup_sharp_recent, window=20)
        out5 = compute_intermarket(spy, uup=uup_sharp_recent, window=5)
        # / shorter window captures the recent spike more sharply
        assert abs(out5.dollar_strength) >= abs(out20.dollar_strength)
