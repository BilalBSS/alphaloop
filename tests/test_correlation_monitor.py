# / tier 3 priority 1: portfolio correlation monitor
# / fires "concentration risk" alerts when positions move together — this is the
# / guard that prevents an "8 uncorrelated" book from turning into "1 big bet"
# / during crises.

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from src.quant.correlation_monitor import (
    CorrelationAlert,
    check_portfolio_correlation,
)


def _mock_pool_with_series(series_map: dict[str, list[float]]):
    # / batched fetch: ANY($1::text[]) returns rows for all requested symbols
    conn = MagicMock()

    async def fake_fetch(sql, symbols):
        out = []
        for sym in symbols:
            prices = series_map.get(sym, [])
            for i, p in enumerate(reversed(prices)):
                out.append({"symbol": sym, "close": p, "date": i})
        return out

    conn.fetch = AsyncMock(side_effect=fake_fetch)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _corr_series(base_returns: np.ndarray, rho: float, noise_scale: float = 0.01,
                 seed: int = 0) -> np.ndarray:
    # / build a price series correlated ~rho with base_returns
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, noise_scale, len(base_returns))
    rets = rho * base_returns + np.sqrt(max(0, 1 - rho * rho)) * noise
    return 100.0 * np.cumprod(1 + rets)


class TestCorrelationAlertDataclass:
    def test_constructs(self):
        a = CorrelationAlert(
            avg_correlation=0.5, max_pair=("A", "B"), max_correlation=0.9,
            high_corr_pairs=[("A", "B", 0.9)], is_concentrated=False,
        )
        assert a.avg_correlation == 0.5
        assert a.max_pair == ("A", "B")


class TestCheckPortfolioCorrelation:
    @pytest.mark.asyncio
    async def test_single_position_returns_none(self):
        pool, _ = _mock_pool_with_series({"AAPL": [100.0] * 30})
        positions = [{"symbol": "AAPL"}]
        result = await check_portfolio_correlation(pool, positions)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_positions_returns_none(self):
        pool, _ = _mock_pool_with_series({})
        result = await check_portfolio_correlation(pool, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_insufficient_history_short_circuits(self):
        # / fewer than 10 bars per symbol → returns_map skips them → <2 series left
        pool, _ = _mock_pool_with_series({"AAPL": [100.0] * 5, "MSFT": [200.0] * 5})
        result = await check_portfolio_correlation(
            pool, [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_highly_correlated_pair_triggers_concentration(self):
        # / build two series with ~0.99 correlation
        rng = np.random.default_rng(42)
        base = rng.normal(0.001, 0.02, 30)
        a = _corr_series(base, rho=0.99, seed=1)
        b = _corr_series(base, rho=0.99, seed=2)
        pool, _ = _mock_pool_with_series({"AAPL": list(a), "MSFT": list(b)})
        result = await check_portfolio_correlation(
            pool, [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
            window=20, threshold=0.85, avg_threshold=0.6,
        )
        assert result is not None
        assert result.max_correlation > 0.85
        assert result.is_concentrated is True
        assert len(result.high_corr_pairs) >= 1
        assert result.high_corr_pairs[0][2] > 0.85

    @pytest.mark.asyncio
    async def test_uncorrelated_pair_not_concentrated(self):
        # / two independent random walks → correlation near 0
        rng = np.random.default_rng(123)
        a = 100.0 * np.cumprod(1 + rng.normal(0.0005, 0.02, 30))
        b = 200.0 * np.cumprod(1 + rng.normal(0.0005, 0.02, 30))
        pool, _ = _mock_pool_with_series({"AAPL": list(a), "MSFT": list(b)})
        result = await check_portfolio_correlation(
            pool, [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
            window=20, threshold=0.85, avg_threshold=0.6,
        )
        assert result is not None
        assert result.is_concentrated is False

    @pytest.mark.asyncio
    async def test_max_pair_reports_highest_correlation(self):
        # / 3 symbols: A&B highly correlated, C independent
        rng = np.random.default_rng(7)
        base = rng.normal(0.001, 0.02, 30)
        a = _corr_series(base, rho=0.99, seed=1)
        b = _corr_series(base, rho=0.99, seed=2)
        c = 100.0 * np.cumprod(1 + rng.normal(0.0, 0.02, 30))
        pool, _ = _mock_pool_with_series({
            "AAPL": list(a), "MSFT": list(b), "GOOG": list(c),
        })
        result = await check_portfolio_correlation(
            pool, [{"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "GOOG"}],
            window=20, threshold=0.85, avg_threshold=0.6,
        )
        assert result is not None
        assert set(result.max_pair) == {"AAPL", "MSFT"}

    @pytest.mark.asyncio
    async def test_accepts_position_object_not_just_dict(self):
        # / the module supports both dict positions and objects with a .symbol attr
        class _Pos:
            def __init__(self, s):
                self.symbol = s

        rng = np.random.default_rng(9)
        prices = list(100.0 * np.cumprod(1 + rng.normal(0.0005, 0.02, 30)))
        prices2 = list(200.0 * np.cumprod(1 + rng.normal(0.0005, 0.02, 30)))
        pool, _ = _mock_pool_with_series({"AAPL": prices, "MSFT": prices2})
        result = await check_portfolio_correlation(
            pool, [_Pos("AAPL"), _Pos("MSFT")],
            window=20, threshold=0.85, avg_threshold=0.6,
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_threshold_params_respected(self):
        # / high corr + high avg_threshold above max corr → not concentrated
        rng = np.random.default_rng(3)
        base = rng.normal(0.001, 0.02, 30)
        a = _corr_series(base, rho=0.90, seed=1)
        b = _corr_series(base, rho=0.90, seed=2)
        pool, _ = _mock_pool_with_series({"AAPL": list(a), "MSFT": list(b)})
        result = await check_portfolio_correlation(
            pool, [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
            window=20, threshold=0.85, avg_threshold=0.99,
        )
        assert result is not None
        # / even though max_corr is high, avg_threshold=0.99 > avg → not concentrated
        assert result.is_concentrated is False
