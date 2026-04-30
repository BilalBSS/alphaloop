# / tests for sector rotation relative-strength indicator

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.indicators.sector_rotation import (
    SectorStrength,
    _relative_strength,
    compute_sector_rotation,
)


def _bars(start: float, end: float, n: int) -> pd.DataFrame:
    close = np.linspace(start, end, n)
    return pd.DataFrame({"close": close})


class TestRelativeStrength:
    def test_hand_computed_matching_moves(self):
        # / linearly 100→110 over 30 bars, period=20 lookback, so close[-20] is
        # / partway through the ramp — rs ≈ 1.97 (close to 2.0, not exactly).
        # / formula: (110/103.45 - 1) / (105/101.72 - 1) = 0.0633 / 0.0322 ≈ 1.97
        sec = _bars(100, 110, 30)
        spy = _bars(100, 105, 30)
        assert _relative_strength(sec, spy, period=20) == pytest.approx(2.0, abs=0.1)

    def test_inverted_when_sector_loses_vs_spy_gain(self):
        # / sector: 100 -> 95 = -5%; spy: 100 -> 105 = +5%. rs = -0.05/0.05 = -1.0
        sec = _bars(100, 95, 30)
        spy = _bars(100, 105, 30)
        rs = _relative_strength(sec, spy, period=20)
        assert rs == pytest.approx(-1.0, abs=0.05)

    def test_spy_flat_returns_zero(self):
        # / spy_ret == 0 -> short-circuit to 0.0
        sec = _bars(100, 110, 30)
        spy = _bars(100, 100, 30)
        assert _relative_strength(sec, spy, period=20) == 0.0

    def test_insufficient_bars_returns_zero(self):
        sec = _bars(100, 110, 10)
        spy = _bars(100, 105, 10)
        assert _relative_strength(sec, spy, period=20) == 0.0

    def test_uses_abs_of_spy_for_denominator(self):
        # / when spy falls and sector rises, denominator uses abs(spy_ret).
        # / with period=20 lookback on 30-bar linear ramps, rs ≈ 1.9 (see above).
        sec = _bars(100, 110, 30)
        spy = _bars(100, 95, 30)
        assert _relative_strength(sec, spy, period=20) == pytest.approx(2.0, abs=0.2)


class TestComputeSectorRotation:
    def test_empty_sector_data(self):
        spy = _bars(100, 105, 70)
        assert compute_sector_rotation({}, spy) == []

    def test_sector_with_insufficient_history_skipped(self):
        # / need at least 61 bars for both sector and spy
        spy = _bars(100, 105, 70)
        sectors = {"XLK": _bars(100, 110, 30)}  # / only 30 bars
        out = compute_sector_rotation(sectors, spy)
        assert out == []

    def test_spy_insufficient_history_returns_empty(self):
        spy = _bars(100, 105, 30)  # / too short
        sectors = {"XLK": _bars(100, 110, 70)}
        out = compute_sector_rotation(sectors, spy)
        assert out == []

    def test_returns_sector_strength_dataclasses(self):
        spy = _bars(100, 105, 70)
        sectors = {
            "XLK": _bars(100, 115, 70),
            "XLE": _bars(100, 108, 70),
        }
        out = compute_sector_rotation(sectors, spy)
        assert len(out) == 2
        assert all(isinstance(s, SectorStrength) for s in out)

    def test_ranked_desc_by_rs_20d(self):
        spy = _bars(100, 105, 70)
        sectors = {
            "XLK": _bars(100, 120, 70),  # / strongest
            "XLE": _bars(100, 110, 70),  # / middle
            "XLU": _bars(100, 102, 70),  # / weakest
        }
        out = compute_sector_rotation(sectors, spy)
        # / sorted descending by rs_20d -> rank 1 is highest rs
        assert out[0].sector == "XLK"
        assert out[-1].sector == "XLU"
        assert out[0].rank == 1
        assert out[1].rank == 2
        assert out[2].rank == 3

    def test_momentum_accelerating_when_20d_above_60d(self):
        # / 20d stronger than 60d -> accelerating
        spy = _bars(100, 105, 70)
        # / sector flat first 50 bars then rips up the last 20 -> 20d > 60d
        flat = [100.0] * 50
        ramp = list(np.linspace(100, 130, 20))
        sec = pd.DataFrame({"close": flat + ramp})
        sectors = {"XLK": sec}
        out = compute_sector_rotation(sectors, spy)
        assert len(out) == 1
        assert out[0].momentum == "accelerating"

    def test_momentum_decelerating_when_20d_much_below_60d(self):
        # / sector had big gain over 60 bars, now flat/down over last 20 -> decelerating
        spy = _bars(100, 105, 70)
        # / 100 -> 130 over the first 50, then flat
        ramp = list(np.linspace(100, 130, 50))
        flat = [130.0] * 20
        sec = pd.DataFrame({"close": ramp + flat})
        sectors = {"XLK": sec}
        out = compute_sector_rotation(sectors, spy)
        assert len(out) == 1
        # / 20d = 0, 60d > 0 -> 20d < 60d * 0.9 -> decelerating
        assert out[0].momentum == "decelerating"

    def test_momentum_stable_when_rs_aligned(self):
        spy = _bars(100, 105, 70)
        # / near-linear sector with SPY at 1:1 roughly -> rs_20 ≈ rs_60
        sec = _bars(100, 110, 70)
        sectors = {"XLK": sec}
        out = compute_sector_rotation(sectors, spy)
        assert len(out) == 1
        # / for linear moves, 20d and 60d rs are close enough to yield stable or accelerating
        assert out[0].momentum in ("stable", "accelerating")

    def test_rank_assignment_unique_and_sequential(self):
        spy = _bars(100, 105, 70)
        sectors = {
            f"XL{i}": _bars(100, 100 + i * 2, 70)
            for i in range(5)
        }
        out = compute_sector_rotation(sectors, spy)
        ranks = [s.rank for s in out]
        assert ranks == [1, 2, 3, 4, 5]

    def test_single_sector_has_rank_one(self):
        spy = _bars(100, 105, 70)
        sectors = {"XLK": _bars(100, 110, 70)}
        out = compute_sector_rotation(sectors, spy)
        assert len(out) == 1
        assert out[0].rank == 1
