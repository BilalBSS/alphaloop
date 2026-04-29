# / tests for ict/smart money indicators

import numpy as np
import pandas as pd

from src.indicators.structure import (
    _find_swing_points,
    fair_value_gaps,
    order_blocks,
    structure_breaks,
)


def _make_series(values):
    return pd.Series(values, dtype=float)


class TestFairValueGaps:
    def test_bullish_fvg(self):
        # / candle 1 high=100, candle 3 low=105 -> gap between 100 and 105
        high = _make_series([100, 103, 108, 110])
        low = _make_series([95, 98, 105, 106])
        close = _make_series([98, 102, 107, 109])
        result = fair_value_gaps(high, low, close)
        bullish = [g for g in result.gaps if g.type == "bullish"]
        assert len(bullish) >= 1
        assert bullish[0].low == 100  # candle 1 high
        assert bullish[0].high == 105  # candle 3 low

    def test_bearish_fvg(self):
        # / candle 1 low=100, candle 3 high=95 -> gap between 95 and 100
        high = _make_series([105, 102, 95, 93])
        low = _make_series([100, 97, 90, 88])
        close = _make_series([102, 98, 92, 90])
        result = fair_value_gaps(high, low, close)
        bearish = [g for g in result.gaps if g.type == "bearish"]
        assert len(bearish) >= 1
        assert bearish[0].high == 100  # candle 1 low
        assert bearish[0].low == 95    # candle 3 high

    def test_fill_detection(self):
        # / bullish fvg at index 1, then price drops to fill it
        high = _make_series([100, 103, 108, 110, 106])
        low = _make_series([95, 98, 105, 106, 100])
        close = _make_series([98, 102, 107, 109, 102])
        result = fair_value_gaps(high, low, close)
        bullish = [g for g in result.gaps if g.type == "bullish"]
        # / last bar low=100 <= gap_high=105, so filled
        assert any(g.filled for g in bullish)

    def test_insufficient_data(self):
        high = _make_series([100, 102])
        low = _make_series([95, 98])
        close = _make_series([98, 101])
        result = fair_value_gaps(high, low, close)
        assert len(result.gaps) == 0

    def test_no_gaps_when_overlapping(self):
        # / candles overlap — no fvg
        high = _make_series([105, 107, 106, 108])
        low = _make_series([100, 102, 101, 103])
        close = _make_series([103, 105, 104, 106])
        result = fair_value_gaps(high, low, close)
        assert len(result.gaps) == 0

    def test_signal_series_shape(self):
        high = _make_series([100, 103, 108])
        low = _make_series([95, 98, 105])
        close = _make_series([98, 102, 107])
        result = fair_value_gaps(high, low, close)
        assert len(result.signal) == 3


class TestOrderBlocks:
    def test_bullish_order_block(self):
        # / bearish candle (close < open) followed by large up impulse
        n = 20
        np.random.seed(42)
        high = _make_series([100 + i * 0.5 for i in range(n)])
        low = _make_series([98 + i * 0.5 for i in range(n)])
        open_ = _make_series([99 + i * 0.5 for i in range(n)])
        close = _make_series([99.5 + i * 0.5 for i in range(n)])

        # / make candle 15 bearish, candle 16 has huge up move
        open_.iloc[15] = 107
        close.iloc[15] = 105  # bearish
        close.iloc[16] = 120  # large impulse up
        high.iloc[16] = 121

        result = order_blocks(high, low, close, open_)
        bullish = [b for b in result.blocks if b.type == "bullish"]
        assert len(bullish) >= 1

    def test_insufficient_data(self):
        high = _make_series([100, 102])
        low = _make_series([98, 99])
        close = _make_series([99, 101])
        open_ = _make_series([98.5, 100])
        result = order_blocks(high, low, close, open_)
        assert len(result.blocks) == 0

    def test_no_blocks_when_no_impulse(self):
        # / all moves are small
        n = 20
        high = _make_series([100 + i * 0.1 for i in range(n)])
        low = _make_series([99 + i * 0.1 for i in range(n)])
        close = _make_series([99.5 + i * 0.1 for i in range(n)])
        open_ = _make_series([99.3 + i * 0.1 for i in range(n)])
        result = order_blocks(high, low, close, open_)
        assert len(result.blocks) == 0

    def test_signal_series_shape(self):
        n = 20
        high = _make_series([100 + i for i in range(n)])
        low = _make_series([98 + i for i in range(n)])
        close = _make_series([99 + i for i in range(n)])
        open_ = _make_series([98.5 + i for i in range(n)])
        result = order_blocks(high, low, close, open_)
        assert len(result.signal) == n


class TestSwingPoints:
    def test_detects_swing_high(self):
        # / peak at index 5
        values = [100, 101, 102, 103, 104, 110, 104, 103, 102, 101, 100]
        high = _make_series(values)
        low = _make_series([v - 2 for v in values])
        sh, _sl = _find_swing_points(high, low, lookback=3)
        # / index 5 should be a swing high
        assert not np.isnan(sh.iloc[5])
        assert sh.iloc[5] == 110

    def test_detects_swing_low(self):
        # / trough at index 5
        values = [110, 108, 106, 104, 102, 95, 102, 104, 106, 108, 110]
        low = _make_series(values)
        high = _make_series([v + 2 for v in values])
        _sh, sl = _find_swing_points(high, low, lookback=3)
        assert not np.isnan(sl.iloc[5])
        assert sl.iloc[5] == 95


class TestStructureBreaks:
    def test_bullish_bos(self):
        # / create clear swing high, then break above it
        # / lookback=3: swing high needs to be highest in 7-bar window
        # / pattern: rise to peak at 6, dip, then break above peak
        close_vals = [100, 102, 104, 106, 108, 110, 115, 108, 106, 104, 102, 104, 106, 120]
        high = _make_series([c + 1 for c in close_vals])
        low = _make_series([c - 1 for c in close_vals])
        close = _make_series(close_vals)
        result = structure_breaks(high, low, close, swing_lookback=3)
        bullish = [b for b in result.breaks if b.direction == "bullish"]
        assert len(bullish) >= 1

    def test_bearish_bos(self):
        # / create clear swing low, then break below it
        close_vals = [120, 118, 116, 114, 112, 110, 105, 112, 114, 116, 118, 116, 114, 100]
        high = _make_series([c + 1 for c in close_vals])
        low = _make_series([c - 1 for c in close_vals])
        close = _make_series(close_vals)
        result = structure_breaks(high, low, close, swing_lookback=3)
        bearish = [b for b in result.breaks if b.direction == "bearish"]
        assert len(bearish) >= 1

    def test_choch_detection(self):
        # / uptrend, then reversal. needs enough data for swing detection on both sides.
        # / swing high ~index 6, swing low ~index 12, break below at end
        close_vals = [
            100, 102, 104, 108, 112, 116, 120,  # uptrend to peak at 6
            114, 110, 106, 102, 98, 94,          # dip to trough at 12
            100, 104, 108,                        # recovery
            102, 98, 94, 85,                      # break below trough
        ]
        high = _make_series([c + 1 for c in close_vals])
        low = _make_series([c - 1 for c in close_vals])
        close = _make_series(close_vals)
        result = structure_breaks(high, low, close, swing_lookback=3)
        assert len(result.breaks) >= 1

    def test_insufficient_data(self):
        high = _make_series([100, 101, 102])
        low = _make_series([98, 99, 100])
        close = _make_series([99, 100, 101])
        result = structure_breaks(high, low, close, swing_lookback=5)
        assert len(result.breaks) == 0

    def test_swing_series_returned(self):
        n = 20
        high = _make_series([100 + i for i in range(n)])
        low = _make_series([98 + i for i in range(n)])
        close = _make_series([99 + i for i in range(n)])
        result = structure_breaks(high, low, close, swing_lookback=3)
        assert len(result.swing_highs) == n
        assert len(result.swing_lows) == n

    def test_signal_values(self):
        n = 20
        high = _make_series([100 + i for i in range(n)])
        low = _make_series([98 + i for i in range(n)])
        close = _make_series([99 + i for i in range(n)])
        result = structure_breaks(high, low, close)
        # / signal should only contain -1, 0, 1
        unique = set(result.signal.unique())
        assert unique.issubset({-1, 0, 1})
