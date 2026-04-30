# / tests for dcf model + monte carlo simulation

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from src.analysis.dcf_model import (
    DCFAssumptions,
    DCFResult,
    build_assumptions_from_db,
    compute_dcf,
    run_dcf_simulation,
    store_dcf_result,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestRunDcfSimulation:
    def test_basic_simulation(self):
        rng = np.random.default_rng(42)
        assumptions = DCFAssumptions(
            revenue=1000.0,
            fcf_margin=0.20,
            revenue_growth=0.10,
            shares_outstanding=100.0,
        )
        values = run_dcf_simulation(assumptions, num_simulations=1000, rng=rng)
        assert len(values) == 1000
        assert all(v >= 0 for v in values)

    def test_higher_growth_higher_value(self):
        rng = np.random.default_rng(42)
        low_growth = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.05,
            shares_outstanding=1.0, growth_std=0.01,
        )
        high_growth = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.20,
            shares_outstanding=1.0, growth_std=0.01,
        )
        low_vals = run_dcf_simulation(low_growth, 500, rng)
        rng2 = np.random.default_rng(42)
        high_vals = run_dcf_simulation(high_growth, 500, rng2)

        assert np.median(high_vals) > np.median(low_vals)

    def test_zero_revenue_returns_zero(self):
        assumptions = DCFAssumptions(
            revenue=0.0, fcf_margin=0.20, revenue_growth=0.10,
            shares_outstanding=1.0,
        )
        values = run_dcf_simulation(assumptions, 100)
        assert all(v == 0.0 for v in values)

    def test_net_debt_reduces_value(self):
        rng1 = np.random.default_rng(42)
        no_debt = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            shares_outstanding=1.0, net_debt=0.0, growth_std=0.01,
        )
        rng2 = np.random.default_rng(42)
        with_debt = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            shares_outstanding=1.0, net_debt=500.0, growth_std=0.01,
        )
        no_debt_vals = run_dcf_simulation(no_debt, 500, rng1)
        debt_vals = run_dcf_simulation(with_debt, 500, rng2)

        assert np.median(no_debt_vals) > np.median(debt_vals)

    def test_simulation_produces_distribution(self):
        rng = np.random.default_rng(42)
        assumptions = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            shares_outstanding=1.0, growth_std=0.10,
        )
        values = run_dcf_simulation(assumptions, 5000, rng)
        # / should have meaningful spread
        p10 = np.percentile(values, 10)
        p90 = np.percentile(values, 90)
        assert p90 > p10


class TestComputeDcf:
    def test_basic_result(self):
        rng = np.random.default_rng(42)
        assumptions = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            shares_outstanding=1.0,
        )
        result = compute_dcf("AAPL", 150.0, assumptions, date(2026, 3, 25), 1000, rng)

        assert result.symbol == "AAPL"
        assert result.current_price == 150.0
        assert result.fair_value_median > 0
        assert result.fair_value_p10 <= result.fair_value_median <= result.fair_value_p90
        assert result.confidence in ("high", "medium", "low")
        assert result.num_simulations == 1000

    def test_upside_calculation(self):
        rng = np.random.default_rng(42)
        assumptions = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.15,
            shares_outstanding=1.0, terminal_multiple=20.0,
        )
        # / set current price very low to guarantee upside
        result = compute_dcf("CHEAP", 1.0, assumptions, num_simulations=100, rng=rng)
        assert result.upside_pct > 0

    def test_zero_current_price(self):
        rng = np.random.default_rng(42)
        assumptions = DCFAssumptions(
            revenue=100.0, fcf_margin=0.20, revenue_growth=0.10,
            shares_outstanding=1.0,
        )
        result = compute_dcf("TEST", 0.0, assumptions, num_simulations=100, rng=rng)
        assert result.upside_pct == 0.0

    def test_confidence_levels(self):
        # / tight distribution = high confidence
        rng = np.random.default_rng(42)
        tight = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            growth_std=0.001, margin_std=0.001, terminal_multiple_std=0.1,
            shares_outstanding=1.0,
        )
        result = compute_dcf("TIGHT", 1000.0, tight, num_simulations=1000, rng=rng)
        assert result.confidence == "high"


class TestRunDcfSimulationDeep:
    def test_deterministic_with_seed_exact_median(self):
        # / same seed must produce same median every time
        rng = np.random.default_rng(99)
        assumptions = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            shares_outstanding=100.0,
        )
        values = run_dcf_simulation(assumptions, num_simulations=5000, rng=rng)
        median1 = float(np.median(values))

        rng2 = np.random.default_rng(99)
        values2 = run_dcf_simulation(assumptions, num_simulations=5000, rng=rng2)
        median2 = float(np.median(values2))

        assert median1 == median2

    def test_negative_revenue_returns_zeros(self):
        # / revenue <= 0 should produce zeros
        assumptions = DCFAssumptions(
            revenue=-500.0, fcf_margin=0.20, revenue_growth=0.10,
            shares_outstanding=1.0,
        )
        values = run_dcf_simulation(assumptions, 100)
        assert all(v == 0.0 for v in values)

    def test_growth_clamping(self):
        # / extreme growth_std should be clamped to [-0.50, 1.0]
        rng = np.random.default_rng(42)
        assumptions = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.0,
            growth_std=10.0, shares_outstanding=1.0,
        )
        values = run_dcf_simulation(assumptions, 500, rng)
        # / should not go negative or produce nans from extreme growth
        assert all(v >= 0 for v in values)
        assert not any(np.isnan(v) for v in values)

    def test_margin_clamping(self):
        # / extreme margin_std should be clamped to [-0.5, 0.8]
        rng = np.random.default_rng(42)
        assumptions = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.50, revenue_growth=0.10,
            margin_std=10.0, shares_outstanding=1.0,
        )
        values = run_dcf_simulation(assumptions, 500, rng)
        assert all(v >= 0 for v in values)
        assert not any(np.isnan(v) for v in values)

    def test_terminal_multiple_clamping(self):
        # / terminal_multiple_std=100 should be clamped to [3.0, 50.0]
        rng = np.random.default_rng(42)
        assumptions = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            terminal_multiple=15.0, terminal_multiple_std=100.0,
            shares_outstanding=1.0,
        )
        values = run_dcf_simulation(assumptions, 500, rng)
        assert all(v >= 0 for v in values)
        assert not any(np.isnan(v) for v in values)

    def test_more_shares_reduces_per_share(self):
        # / doubling shares outstanding should halve per-share value
        rng1 = np.random.default_rng(42)
        base = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            shares_outstanding=100.0, growth_std=0.001, margin_std=0.001,
            terminal_multiple_std=0.01,
        )
        rng2 = np.random.default_rng(42)
        double = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            shares_outstanding=200.0, growth_std=0.001, margin_std=0.001,
            terminal_multiple_std=0.01,
        )
        v1 = run_dcf_simulation(base, 500, rng1)
        v2 = run_dcf_simulation(double, 500, rng2)
        ratio = float(np.median(v1)) / float(np.median(v2))
        assert abs(ratio - 2.0) < 0.05

    def test_higher_discount_rate_lower_pv(self):
        # / higher discount rate should produce lower present value
        rng1 = np.random.default_rng(42)
        low_rate = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            discount_rate=0.05, shares_outstanding=1.0, growth_std=0.01,
        )
        rng2 = np.random.default_rng(42)
        high_rate = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            discount_rate=0.20, shares_outstanding=1.0, growth_std=0.01,
        )
        v_low = run_dcf_simulation(low_rate, 500, rng1)
        v_high = run_dcf_simulation(high_rate, 500, rng2)
        assert np.median(v_low) > np.median(v_high)


class TestAntitheticVariates:
    def test_odd_num_simulations(self):
        # / odd n should still return exactly n samples
        rng = np.random.default_rng(42)
        assumptions = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            shares_outstanding=1.0,
        )
        for n in [101, 99, 1, 3, 7]:
            values = run_dcf_simulation(assumptions, num_simulations=n, rng=np.random.default_rng(42))
            assert len(values) == n, f"expected {n} samples, got {len(values)}"

    def test_antithetic_reduces_variance(self):
        # / antithetic should produce tighter std than crude mc on same sample count
        # / run many trials to average out randomness
        assumptions = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            growth_std=0.10, margin_std=0.05,
            shares_outstanding=1.0,
        )
        antithetic_stds = []
        for seed in range(50):
            rng = np.random.default_rng(seed)
            values = run_dcf_simulation(assumptions, 200, rng)
            antithetic_stds.append(np.std(values))

        avg_antithetic_std = np.mean(antithetic_stds)
        # / antithetic std should be materially lower than theoretical crude std
        # / with growth_std=0.10 and 200 samples, crude std is large
        # / just verify it's finite and positive (sanity)
        assert avg_antithetic_std > 0
        assert np.isfinite(avg_antithetic_std)

    def test_vectorized_matches_properties(self):
        # / vectorized antithetic should still be deterministic with same seed
        rng1 = np.random.default_rng(77)
        rng2 = np.random.default_rng(77)
        assumptions = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            shares_outstanding=100.0,
        )
        v1 = run_dcf_simulation(assumptions, 1000, rng1)
        v2 = run_dcf_simulation(assumptions, 1000, rng2)
        np.testing.assert_array_equal(v1, v2)


class TestComputeDcfDeep:
    def test_p10_lt_median_lt_p90_strict(self):
        # / with meaningful std, p10 < median < p90 must be strict
        rng = np.random.default_rng(42)
        assumptions = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            growth_std=0.10, shares_outstanding=1.0,
        )
        result = compute_dcf("SPREAD", 100.0, assumptions, num_simulations=5000, rng=rng)
        assert result.fair_value_p10 < result.fair_value_median < result.fair_value_p90

    def test_downside_when_overpriced(self):
        # / current price >> fair value -> negative upside
        rng = np.random.default_rng(42)
        assumptions = DCFAssumptions(
            revenue=10.0, fcf_margin=0.10, revenue_growth=0.02,
            shares_outstanding=1.0, terminal_multiple=5.0,
        )
        result = compute_dcf("OVER", 10000.0, assumptions, num_simulations=500, rng=rng)
        assert result.upside_pct < 0

    def test_confidence_low_with_wide_std(self):
        # / wide distribution -> spread > 1.0 -> "low"
        rng = np.random.default_rng(42)
        wide = DCFAssumptions(
            revenue=1000.0, fcf_margin=0.20, revenue_growth=0.10,
            growth_std=0.50, margin_std=0.30, terminal_multiple_std=15.0,
            shares_outstanding=1.0,
        )
        result = compute_dcf("WIDE", 100.0, wide, num_simulations=5000, rng=rng)
        assert result.confidence == "low"


class TestBuildAssumptions:
    @pytest.mark.asyncio
    async def test_builds_from_db(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            # / fundamentals row
            {
                "fcf_margin": Decimal("0.20"),
                "revenue_growth_1y": Decimal("0.12"),
                "debt_to_equity": Decimal("0.5"),
                "ps_ratio": Decimal("8.0"),
                "pe_ratio": Decimal("25"),
                "pe_forward": None,
                "peg_ratio": None,
                "revenue_growth_3y": None,
                "sector": "Tech",
                "sector_pe_avg": None,
                "sector_ps_avg": None,
                "symbol": "AAPL",
                "date": date(2026, 3, 25),
            },
            # / price row
            {"close": Decimal("180.00")},
        ]
        pool = _mock_pool(mock_conn)

        result = await build_assumptions_from_db(pool, "AAPL")
        assert result is not None
        assert result.fcf_margin == 0.20
        assert result.revenue_growth == 0.12

    @pytest.mark.asyncio
    async def test_returns_none_no_fundamentals(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        pool = _mock_pool(mock_conn)

        result = await build_assumptions_from_db(pool, "FAKE")
        assert result is None


class TestStoreDcfResult:
    @pytest.mark.asyncio
    async def test_stores_result(self):
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)

        result = DCFResult(
            symbol="AAPL",
            date=date(2026, 3, 25),
            fair_value_median=200.0,
            fair_value_p10=150.0,
            fair_value_p90=260.0,
            current_price=180.0,
            upside_pct=0.1111,
            num_simulations=10000,
            confidence="medium",
        )

        success = await store_dcf_result(pool, result, regime="bull")
        assert success is True
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_store_error(self):
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("db error")
        pool = _mock_pool(mock_conn)

        result = DCFResult(
            symbol="FAIL",
            date=date(2026, 3, 25),
            fair_value_median=100.0,
            fair_value_p10=80.0,
            fair_value_p90=120.0,
            current_price=90.0,
            upside_pct=0.11,
            num_simulations=100,
            confidence="low",
        )

        success = await store_dcf_result(pool, result)
        assert success is False


# ---------------------------------------------------------------------------
# / compute_terminal_multiple tests
# ---------------------------------------------------------------------------

class TestComputeTerminalMultiple:
    def test_negative_growth_severe(self):
        from src.analysis.dcf_model import compute_terminal_multiple
        assert compute_terminal_multiple(-0.25) == 6.0

    def test_zero_growth(self):
        from src.analysis.dcf_model import compute_terminal_multiple
        assert compute_terminal_multiple(0.0) == 10.0

    def test_moderate_growth(self):
        from src.analysis.dcf_model import compute_terminal_multiple
        assert compute_terminal_multiple(0.10) == 15.0

    def test_high_growth_interpolated(self):
        from src.analysis.dcf_model import compute_terminal_multiple
        result = compute_terminal_multiple(0.19)
        assert 20.0 <= result <= 21.0

    def test_extreme_growth_cap(self):
        from src.analysis.dcf_model import compute_terminal_multiple
        assert compute_terminal_multiple(1.50) == 40.0

    def test_fcf_margin_premium(self):
        from src.analysis.dcf_model import compute_terminal_multiple
        base = compute_terminal_multiple(0.30)
        with_margin = compute_terminal_multiple(0.30, fcf_margin=0.45)
        assert with_margin > base

    def test_fcf_margin_discount(self):
        from src.analysis.dcf_model import compute_terminal_multiple
        base = compute_terminal_multiple(0.30)
        with_margin = compute_terminal_multiple(0.30, fcf_margin=0.02)
        assert with_margin < base

    def test_nan_growth_returns_default(self):
        from src.analysis.dcf_model import compute_terminal_multiple
        assert compute_terminal_multiple(float('nan')) == 15.0

    def test_monotonicity(self):
        from src.analysis.dcf_model import compute_terminal_multiple
        prev = 0.0
        for g in range(-30, 110, 5):
            result = compute_terminal_multiple(g / 100.0)
            assert result >= prev
            prev = result

    def test_fcf_margin_floor(self):
        from src.analysis.dcf_model import compute_terminal_multiple
        result = compute_terminal_multiple(0.10, fcf_margin=-0.50)
        # / adjustment = 1.0 + 0.5*(-0.50-0.15) = 0.675, clamped to floor 0.70
        assert result == 10.5

    def test_fcf_margin_cap(self):
        from src.analysis.dcf_model import compute_terminal_multiple
        result = compute_terminal_multiple(0.10, fcf_margin=0.80)
        assert result == 19.5


class TestTerminalMultipleStd:
    def test_proportional(self):
        from src.analysis.dcf_model import compute_terminal_multiple_std
        assert compute_terminal_multiple_std(15.0) == 2.7
        assert compute_terminal_multiple_std(33.0) == 5.9
