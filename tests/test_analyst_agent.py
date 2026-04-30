# / tests for analyst agent

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.analyst_agent import AnalystAgent
from src.analysis.ai_summary import AnalysisSummary, DualAnalysis
from src.analysis.dcf_model import DCFResult
from src.analysis.earnings_signals import EarningsSignal
from src.analysis.insider_activity import InsiderSignal
from src.analysis.ratio_analysis import RatioScore

# ---------------------------------------------------------------------------
# / helpers
# ---------------------------------------------------------------------------

def _mock_pool(mock_conn=None):
    if mock_conn is None:
        mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


def _make_ratio(composite: float = 70.0) -> RatioScore:
    return RatioScore(
        symbol="AAPL", date=date.today(),
        pe_score=80.0, ps_score=60.0, peg_score=75.0,
        fcf_margin_score=65.0, debt_equity_score=70.0,
        composite_score=composite,
        details={
            "pe_ratio": "15.0", "ps_ratio": "5.0", "peg_ratio": "1.2",
            "fcf_margin": "0.20", "debt_to_equity": "0.5",
        },
    )


def _make_dcf(upside: float = 0.25) -> DCFResult:
    return DCFResult(
        symbol="AAPL", date=date.today(),
        fair_value_median=180.0, fair_value_p10=140.0, fair_value_p90=220.0,
        current_price=150.0, upside_pct=upside,
        num_simulations=10000, confidence="high",
    )


def _make_earnings(strength: float = 60.0) -> EarningsSignal:
    return EarningsSignal(
        symbol="AAPL", date=date.today(),
        signal="bullish", strength=strength,
        surprise_pct=0.08, consecutive_beats=3,
        avg_surprise_4q=0.06,
    )


def _make_insider(strength: float = 40.0) -> InsiderSignal:
    return InsiderSignal(
        symbol="AAPL", date=date.today(),
        signal="bullish", strength=strength,
        net_buy_ratio=0.6, total_buys=5, total_sells=2,
        buy_value=500000, sell_value=100000,
        cluster_detected=True, unique_buyers=4, unique_sellers=1,
    )


def _make_summary() -> AnalysisSummary:
    return AnalysisSummary(
        symbol="AAPL", date=date.today(),
        summary="AAPL looks bullish", model_used=None,
        signal="bullish", confidence=70.0,
    )


def _make_dual_analysis() -> DualAnalysis:
    return DualAnalysis(
        groq=_make_summary(),
        deepseek=None,
        consensus="bullish",
    )


# ---------------------------------------------------------------------------
# / _compute_fundamental_score
# ---------------------------------------------------------------------------

class TestComputeFundamentalScore:
    def setup_method(self):
        self.agent = AnalystAgent()

    def test_all_components(self):
        # / hand-computed: ratio 70*0.35 + dcf_score*0.25 + earnings 60*0.20 + insider 40*0.20
        # / dcf upside 0.25 -> score = (0.25+0.5)/1.0*100 = 75
        # / weighted = 70*0.35 + 75*0.25 + 60*0.20 + 40*0.20 = 24.5+18.75+12+8 = 63.25
        # / total_weight = 1.0, result = 63.25 -> 63.2 (round to 1)
        score = self.agent._compute_fundamental_score(
            _make_ratio(70.0), _make_dcf(0.25), _make_earnings(60.0), _make_insider(40.0),
        )
        assert score == pytest.approx(63.2, abs=0.1)

    def test_partial_ratio_and_earnings(self):
        # / only ratio (70) and earnings (60)
        # / reweighted: ratio 70*0.35 + earnings 60*0.20 = 24.5+12 = 36.5
        # / total_weight = 0.35+0.20 = 0.55
        # / result = 36.5 / 0.55 = 66.36..
        score = self.agent._compute_fundamental_score(
            _make_ratio(70.0), None, _make_earnings(60.0), None,
        )
        assert score == pytest.approx(66.4, abs=0.1)

    def test_none_returns_none(self):
        score = self.agent._compute_fundamental_score(None, None, None, None)
        assert score is None

    def test_ratio_only(self):
        score = self.agent._compute_fundamental_score(_make_ratio(80.0), None, None, None)
        assert score == 80.0

    def test_dcf_upside_normalization_negative_50(self):
        # / upside = -0.50 -> score = (-0.50+0.5)/1.0*100 = 0
        score = self.agent._compute_fundamental_score(None, _make_dcf(-0.50), None, None)
        assert score == 0.0

    def test_dcf_upside_normalization_positive_50(self):
        # / upside = 0.50 -> score = (0.50+0.5)/1.0*100 = 100
        score = self.agent._compute_fundamental_score(None, _make_dcf(0.50), None, None)
        assert score == 100.0

    def test_dcf_upside_normalization_zero(self):
        # / upside = 0.0 -> score = (0.0+0.5)/1.0*100 = 50
        score = self.agent._compute_fundamental_score(None, _make_dcf(0.0), None, None)
        assert score == 50.0

    def test_dcf_upside_clamped_above(self):
        # / upside = 1.0 -> score = (1.0+0.5)/1.0*100 = 150 -> clamped to 100
        score = self.agent._compute_fundamental_score(None, _make_dcf(1.0), None, None)
        assert score == 100.0

    def test_dcf_upside_clamped_below(self):
        # / upside = -1.0 -> score = (-1.0+0.5)/1.0*100 = -50 -> clamped to 0
        score = self.agent._compute_fundamental_score(None, _make_dcf(-1.0), None, None)
        assert score == 0.0

    def test_ratio_none_composite_ignored(self):
        r = _make_ratio(70.0)
        r.composite_score = None
        score = self.agent._compute_fundamental_score(r, None, _make_earnings(60.0), None)
        # / only earnings contributes
        assert score == 60.0


# ---------------------------------------------------------------------------
# / _build_details
# ---------------------------------------------------------------------------

class TestBuildDetails:
    def setup_method(self):
        self.agent = AnalystAgent()

    def test_includes_all_fields(self):
        d = self.agent._build_details(
            _make_ratio(), _make_dcf(), _make_earnings(), _make_insider(), _make_summary(),
        )
        # / ratio fields
        assert "pe_ratio" in d
        assert "ps_ratio" in d
        assert "peg_ratio" in d
        assert "fcf_margin" in d
        assert "debt_to_equity" in d
        assert "ratio_composite" in d
        # / dcf fields
        assert "dcf_upside" in d
        assert "dcf_median" in d
        assert "dcf_confidence" in d
        # / earnings fields
        assert "earnings_surprise_pct" in d
        assert "consecutive_beats" in d
        assert "earnings_signal" in d
        # / insider fields
        assert "insider_net_buy_ratio" in d
        assert "insider_signal" in d
        # / summary fields
        assert "summary" in d
        assert "summary_signal" in d

    def test_empty_when_all_none(self):
        d = self.agent._build_details(None, None, None, None, None)
        assert d == {}


# ---------------------------------------------------------------------------
# / run (integration-level with mocks)
# ---------------------------------------------------------------------------

class TestAnalystAgentRun:
    def setup_method(self):
        self.agent = AnalystAgent()

    @pytest.mark.asyncio
    async def test_run_all_succeed(self):
        mock_conn = AsyncMock()
        # / regime query
        mock_conn.fetchrow.return_value = {"regime": "bull", "confidence": 0.8}
        pool = _mock_pool(mock_conn)

        with (
            patch("src.agents.analyst_agent.analyze_ratios", new_callable=AsyncMock, return_value=_make_ratio()),
            patch("src.agents.analyst_agent.analyze_dcf", new_callable=AsyncMock, return_value=_make_dcf()),
            patch("src.agents.analyst_agent.analyze_earnings", new_callable=AsyncMock, return_value=_make_earnings()),
            patch("src.agents.analyst_agent.analyze_insider_activity", new_callable=AsyncMock, return_value=_make_insider()),
            patch("src.agents.analyst_agent.generate_dual_analysis", new_callable=AsyncMock, return_value=_make_dual_analysis()),
            patch("src.agents.analyst_agent.store_analysis_score", new_callable=AsyncMock, return_value=1) as mock_store,
        ):
            results = await self.agent.run(pool, ["AAPL"])

        assert "AAPL" in results
        assert results["AAPL"] is not None
        mock_store.assert_called_once()
        call_kwargs = mock_store.call_args
        assert call_kwargs.kwargs["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_run_partial_failure(self):
        # / ratio fails, others succeed -> score computed from available
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"regime": "bull", "confidence": 0.8}
        pool = _mock_pool(mock_conn)

        with (
            patch("src.agents.analyst_agent.analyze_ratios", new_callable=AsyncMock, side_effect=Exception("ratio failed")),
            patch("src.agents.analyst_agent.analyze_dcf", new_callable=AsyncMock, return_value=_make_dcf()),
            patch("src.agents.analyst_agent.analyze_earnings", new_callable=AsyncMock, return_value=_make_earnings()),
            patch("src.agents.analyst_agent.analyze_insider_activity", new_callable=AsyncMock, return_value=_make_insider()),
            patch("src.agents.analyst_agent.generate_dual_analysis", new_callable=AsyncMock, return_value=_make_dual_analysis()),
            patch("src.agents.analyst_agent.store_analysis_score", new_callable=AsyncMock, return_value=1),
        ):
            results = await self.agent.run(pool, ["AAPL"])

        assert results["AAPL"] is not None  # / still computed from dcf+earnings+insider

    @pytest.mark.asyncio
    async def test_run_all_fail(self):
        # / all analysis raise, score is None
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None  # / no regime
        pool = _mock_pool(mock_conn)

        with (
            patch("src.agents.analyst_agent.analyze_ratios", new_callable=AsyncMock, side_effect=Exception("fail")),
            patch("src.agents.analyst_agent.analyze_dcf", new_callable=AsyncMock, side_effect=Exception("fail")),
            patch("src.agents.analyst_agent.analyze_earnings", new_callable=AsyncMock, side_effect=Exception("fail")),
            patch("src.agents.analyst_agent.analyze_insider_activity", new_callable=AsyncMock, side_effect=Exception("fail")),
            patch("src.agents.analyst_agent.generate_dual_analysis", new_callable=AsyncMock, return_value=_make_dual_analysis()),
            patch("src.agents.analyst_agent.store_analysis_score", new_callable=AsyncMock, return_value=1),
        ):
            results = await self.agent.run(pool, ["AAPL"])

        assert results["AAPL"] is None

    @pytest.mark.asyncio
    async def test_run_empty_symbols(self):
        pool = _mock_pool()
        results = await self.agent.run(pool, [])
        assert results == {}

    @pytest.mark.asyncio
    async def test_silent_timeout_now_logs_event(self):
        import asyncio as _asyncio

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"regime": "bull", "confidence": 0.8}
        pool = _mock_pool(mock_conn)

        async def _slow(*args, **kwargs):
            await _asyncio.sleep(0.5)
            return None

        with (
            patch.object(self.agent, "_analyze_symbol", side_effect=_slow),
            patch("src.agents.analyst_agent.log_event", new_callable=AsyncMock) as mock_log,
        ):
            await self.agent.run(pool, ["AAPL"], per_symbol_timeout_s=0.05, inter_symbol_sleep_s=0.0)

        timeout_calls = [
            c for c in mock_log.call_args_list
            if "timeout after" in str(c)
        ]
        assert len(timeout_calls) >= 1

    @pytest.mark.asyncio
    async def test_zero_success_cycle_escalates_to_warning(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"regime": "bull", "confidence": 0.8}
        pool = _mock_pool(mock_conn)

        async def _always_fail(*args, **kwargs):
            raise RuntimeError("simulated failure")

        with (
            patch.object(self.agent, "_analyze_symbol", side_effect=_always_fail),
            patch("src.agents.analyst_agent.log_event", new_callable=AsyncMock) as mock_log,
        ):
            await self.agent.run(pool, ["AAPL", "MSFT"], inter_symbol_sleep_s=0.0)

        cycle_calls = [
            c for c in mock_log.call_args_list
            if len(c.args) >= 4 and "cycle complete" in str(c.args[3])
        ]
        assert len(cycle_calls) >= 1
        cycle_call = cycle_calls[-1]
        assert cycle_call.args[1] == "warning"

    @pytest.mark.asyncio
    async def test_regime_fetched_from_db(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"regime": "bear", "confidence": 0.9}
        pool = _mock_pool(mock_conn)

        with (
            patch("src.agents.analyst_agent.analyze_ratios", new_callable=AsyncMock, return_value=_make_ratio()),
            patch("src.agents.analyst_agent.analyze_dcf", new_callable=AsyncMock, return_value=_make_dcf()),
            patch("src.agents.analyst_agent.analyze_earnings", new_callable=AsyncMock, return_value=_make_earnings()),
            patch("src.agents.analyst_agent.analyze_insider_activity", new_callable=AsyncMock, return_value=_make_insider()),
            patch("src.agents.analyst_agent.generate_dual_analysis", new_callable=AsyncMock, return_value=_make_dual_analysis()) as mock_dual,
            patch("src.agents.analyst_agent.store_analysis_score", new_callable=AsyncMock, return_value=1) as mock_store,
        ):
            await self.agent.run(pool, ["AAPL"])

        # / regime passed to generate_dual_analysis
        mock_dual.assert_called_once()
        call_kwargs = mock_dual.call_args
        assert call_kwargs.kwargs["regime"] == "bear"
        # / regime passed to store_analysis_score
        store_kwargs = mock_store.call_args.kwargs
        assert store_kwargs["regime"] == "bear"

    @pytest.mark.asyncio
    async def test_symbol_failure_continues_to_next(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"regime": "bull", "confidence": 0.8}
        pool = _mock_pool(mock_conn)

        call_count = 0

        async def _ratios_side_effect(pool, symbol):
            nonlocal call_count
            call_count += 1
            if symbol == "BAD":
                raise Exception("bad symbol")
            return _make_ratio()

        with (
            patch("src.agents.analyst_agent.analyze_ratios", side_effect=_ratios_side_effect),
            patch("src.agents.analyst_agent.analyze_dcf", new_callable=AsyncMock, return_value=_make_dcf()),
            patch("src.agents.analyst_agent.analyze_earnings", new_callable=AsyncMock, return_value=_make_earnings()),
            patch("src.agents.analyst_agent.analyze_insider_activity", new_callable=AsyncMock, return_value=_make_insider()),
            patch("src.agents.analyst_agent.generate_dual_analysis", new_callable=AsyncMock, return_value=_make_dual_analysis()),
            patch("src.agents.analyst_agent.store_analysis_score", new_callable=AsyncMock, return_value=1),
        ):
            results = await self.agent.run(pool, ["AAPL", "MSFT"])

        assert len(results) == 2
        assert results["AAPL"] is not None
        assert results["MSFT"] is not None

    @pytest.mark.asyncio
    async def test_store_called_with_correct_composite(self):
        # / verify composite_score matches fundamental_score when no technical
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None  # / no regime
        pool = _mock_pool(mock_conn)

        with (
            patch("src.agents.analyst_agent.analyze_ratios", new_callable=AsyncMock, return_value=_make_ratio(80.0)),
            patch("src.agents.analyst_agent.analyze_dcf", new_callable=AsyncMock, return_value=None),
            patch("src.agents.analyst_agent.analyze_earnings", new_callable=AsyncMock, return_value=None),
            patch("src.agents.analyst_agent.analyze_insider_activity", new_callable=AsyncMock, return_value=None),
            patch("src.agents.analyst_agent.generate_dual_analysis", new_callable=AsyncMock, return_value=_make_dual_analysis()),
            patch("src.agents.analyst_agent.store_analysis_score", new_callable=AsyncMock, return_value=1) as mock_store,
        ):
            await self.agent.run(pool, ["AAPL"])

        kw = mock_store.call_args.kwargs
        assert kw["fundamental_score"] == 80.0
        assert kw["composite_score"] == 80.0
        assert kw["technical_score"] is None

    @pytest.mark.asyncio
    async def test_used_fundamentals_flag(self):
        # / if ratio_score or dcf_result is present, used_fundamentals=True
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        pool = _mock_pool(mock_conn)

        with (
            patch("src.agents.analyst_agent.analyze_ratios", new_callable=AsyncMock, return_value=_make_ratio()),
            patch("src.agents.analyst_agent.analyze_dcf", new_callable=AsyncMock, return_value=None),
            patch("src.agents.analyst_agent.analyze_earnings", new_callable=AsyncMock, return_value=None),
            patch("src.agents.analyst_agent.analyze_insider_activity", new_callable=AsyncMock, return_value=None),
            patch("src.agents.analyst_agent.generate_dual_analysis", new_callable=AsyncMock, return_value=_make_dual_analysis()),
            patch("src.agents.analyst_agent.store_analysis_score", new_callable=AsyncMock, return_value=1) as mock_store,
        ):
            await self.agent.run(pool, ["AAPL"])

        assert mock_store.call_args.kwargs["used_fundamentals"] is True


# ---------------------------------------------------------------------------
# / _compute_technical_score (bug 4c: null technical_score breaking composite)
# ---------------------------------------------------------------------------

class TestComputeTechnicalScore:
    def test_none_for_empty_indicators(self):
        from src.agents.analyst_agent import _compute_technical_score
        assert _compute_technical_score(None) is None
        assert _compute_technical_score({}) is None

    def test_uses_rsi14_key_not_rsi(self):
        # / bug 4c root cause: function looked up "rsi" but enrichment stores "rsi14"
        from src.agents.analyst_agent import _compute_technical_score
        # / oversold rsi14=25 -> direction score = 100 - 25 = 75 -> bullish
        score = _compute_technical_score({"rsi14": 25.0})
        assert score is not None
        assert score > 50.0  # / oversold = bullish

    def test_falls_back_to_rsi_key(self):
        # / backwards-compat: if "rsi14" missing, still accept "rsi"
        from src.agents.analyst_agent import _compute_technical_score
        score = _compute_technical_score({"rsi": 25.0})
        assert score is not None
        assert score > 50.0

    def test_overbought_rsi_bearish(self):
        from src.agents.analyst_agent import _compute_technical_score
        score = _compute_technical_score({"rsi14": 80.0})
        assert score is not None
        assert score < 50.0

    def test_neutral_rsi_near_midline(self):
        from src.agents.analyst_agent import _compute_technical_score
        score = _compute_technical_score({"rsi14": 50.0})
        assert score is not None
        assert abs(score - 50.0) < 2.0

    def test_macd_histogram_positive_bullish(self):
        from src.agents.analyst_agent import _compute_technical_score
        score = _compute_technical_score({"macd_histogram": 1.0})
        assert score is not None
        assert score > 50.0

    def test_macd_histogram_negative_bearish(self):
        from src.agents.analyst_agent import _compute_technical_score
        score = _compute_technical_score({"macd_histogram": -1.0})
        assert score is not None
        assert score < 50.0

    def test_adx_amplifies_strong_trend(self):
        # / bug 4c fix: adx is a confidence multiplier, NOT a directional input
        # / with rsi=30 (direction=70), strong adx should push score further from 50
        from src.agents.analyst_agent import _compute_technical_score
        weak = _compute_technical_score({"rsi14": 30.0, "adx": 10.0})
        strong = _compute_technical_score({"rsi14": 30.0, "adx": 40.0})
        assert weak is not None and strong is not None
        # / strong trend amplifies directional signal
        assert strong > weak

    def test_adx_alone_is_not_directional(self):
        # / adx alone (no rsi/macd) must return None — adx is multiplier only
        from src.agents.analyst_agent import _compute_technical_score
        assert _compute_technical_score({"adx": 40.0}) is None

    def test_clamps_to_0_100(self):
        from src.agents.analyst_agent import _compute_technical_score
        score = _compute_technical_score({"rsi14": 0.0, "macd_histogram": 100.0, "adx": 100.0})
        assert 0.0 <= score <= 100.0
        score_low = _compute_technical_score({"rsi14": 100.0, "macd_histogram": -100.0, "adx": 100.0})
        assert 0.0 <= score_low <= 100.0

    def test_ignores_non_numeric_gracefully(self):
        from src.agents.analyst_agent import _compute_technical_score
        score = _compute_technical_score({"rsi14": "not_a_number", "macd_histogram": 0.5})
        # / rsi ignored, macd contributes -> some score
        assert score is not None


class TestKronosComposite:
    # / phase 6 step 8: kronos is blended into composite at weight _kronos_weight() (default 0.15)

    def test_kronos_weight_default_is_0_15(self):
        import os

        from src.agents.analyst_agent import _kronos_weight
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KRONOS_COMPOSITE_WEIGHT", None)
            assert _kronos_weight() == 0.15

    def test_kronos_weight_reads_env(self):
        import os

        from src.agents.analyst_agent import _kronos_weight
        with patch.dict(os.environ, {"KRONOS_COMPOSITE_WEIGHT": "0.25"}):
            assert _kronos_weight() == 0.25

    def test_kronos_weight_clamped_to_0_0_5(self):
        import os

        from src.agents.analyst_agent import _kronos_weight
        with patch.dict(os.environ, {"KRONOS_COMPOSITE_WEIGHT": "1.5"}):
            assert _kronos_weight() == 0.5
        with patch.dict(os.environ, {"KRONOS_COMPOSITE_WEIGHT": "-0.5"}):
            assert _kronos_weight() == 0.0

    def test_kronos_weight_invalid_falls_back(self):
        import os

        from src.agents.analyst_agent import _kronos_weight
        with patch.dict(os.environ, {"KRONOS_COMPOSITE_WEIGHT": "not_a_number"}):
            assert _kronos_weight() == 0.15

    @pytest.mark.asyncio
    async def test_compute_kronos_score_returns_none_for_short_history(self):
        from src.agents.analyst_agent import _compute_kronos_score
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        pool = _mock_pool(conn)
        score, details = await _compute_kronos_score(pool, "AAPL")
        assert score is None
        assert details is None

    @pytest.mark.asyncio
    async def test_compute_kronos_score_with_adequate_history(self):
        # / build 60 rows of synthetic ohlcv with mild uptrend; expect a score in [0, 100]
        from src.agents.analyst_agent import _compute_kronos_score
        rows = [{
            "open": 100 + i,
            "high": 101 + i,
            "low":  99 + i,
            "close": 100.5 + i,
            "volume": 1000000,
        } for i in range(60)]
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=rows)
        pool = _mock_pool(conn)
        score, details = await _compute_kronos_score(pool, "AAPL")
        assert score is not None
        assert 0.0 <= score <= 100.0
        assert details is not None
        assert details.get("source") in ("kronos_hf", "fallback_heuristic")


# ---------------------------------------------------------------------------
# / phase 9: batched staleness-ordered analyst cycle
# ---------------------------------------------------------------------------

import asyncio
from datetime import datetime, timedelta, timezone

from src.agents.analyst_agent import (
    get_coverage_pct,
    order_symbols_by_staleness,
)


class TestOrderSymbolsByStaleness:
    @pytest.mark.asyncio
    async def test_never_scored_first(self):
        # / symbols with no row in analysis_scores jump to the front of the queue
        now = datetime.now(tz=timezone.utc)
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            {"symbol": "AAPL", "last_scored": now - timedelta(hours=1)},
            # / MSFT and NVDA have never been scored
        ])
        pool = _mock_pool(conn)
        out = await order_symbols_by_staleness(pool, ["AAPL", "MSFT", "NVDA"])
        # / never-scored preserve input order, then oldest-scored after
        assert out[:2] == ["MSFT", "NVDA"]
        assert out[2] == "AAPL"

    @pytest.mark.asyncio
    async def test_oldest_first(self):
        # / multiple scored symbols — sort by oldest last_scored first
        now = datetime.now(tz=timezone.utc)
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            {"symbol": "A", "last_scored": now - timedelta(hours=2)},
            {"symbol": "B", "last_scored": now - timedelta(hours=5)},
            {"symbol": "C", "last_scored": now - timedelta(hours=1)},
        ])
        pool = _mock_pool(conn)
        out = await order_symbols_by_staleness(pool, ["A", "B", "C"])
        # / B (5h old), A (2h old), C (1h old)
        assert out == ["B", "A", "C"]

    @pytest.mark.asyncio
    async def test_min_refresh_interval_drops_recent(self):
        # / symbols scored within min_refresh_interval_s are excluded entirely
        now = datetime.now(tz=timezone.utc)
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            {"symbol": "FRESH", "last_scored": now - timedelta(minutes=10)},
            {"symbol": "STALE", "last_scored": now - timedelta(hours=2)},
        ])
        pool = _mock_pool(conn)
        # / 30-min min interval: FRESH (10 min) excluded, STALE (2h) included
        out = await order_symbols_by_staleness(
            pool, ["FRESH", "STALE"], min_refresh_interval_s=1800.0,
        )
        assert out == ["STALE"]

    @pytest.mark.asyncio
    async def test_min_refresh_zero_keeps_all(self):
        # / min_refresh_interval_s=0 disables the skip
        now = datetime.now(tz=timezone.utc)
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            {"symbol": "X", "last_scored": now - timedelta(seconds=30)},
        ])
        pool = _mock_pool(conn)
        out = await order_symbols_by_staleness(pool, ["X"], min_refresh_interval_s=0.0)
        assert out == ["X"]

    @pytest.mark.asyncio
    async def test_empty_symbols_returns_empty(self):
        pool = _mock_pool()
        out = await order_symbols_by_staleness(pool, [])
        assert out == []

    @pytest.mark.asyncio
    async def test_db_error_falls_back_to_input_order(self):
        # / on db exception return input order unchanged — don't starve the caller
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=Exception("db is down"))
        pool = _mock_pool(conn)
        out = await order_symbols_by_staleness(pool, ["AAPL", "MSFT"])
        assert out == ["AAPL", "MSFT"]


class TestGetCoveragePct:
    @pytest.mark.asyncio
    async def test_coverage_all_fresh(self):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"fresh": 3})
        pool = _mock_pool(conn)
        pct = await get_coverage_pct(pool, ["A", "B", "C"], window_s=3600)
        assert pct == 1.0

    @pytest.mark.asyncio
    async def test_coverage_half(self):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"fresh": 2})
        pool = _mock_pool(conn)
        pct = await get_coverage_pct(pool, ["A", "B", "C", "D"], window_s=3600)
        assert pct == 0.5

    @pytest.mark.asyncio
    async def test_coverage_none_fresh(self):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"fresh": 0})
        pool = _mock_pool(conn)
        pct = await get_coverage_pct(pool, ["A", "B"], window_s=3600)
        assert pct == 0.0

    @pytest.mark.asyncio
    async def test_coverage_empty_universe(self):
        pool = _mock_pool()
        pct = await get_coverage_pct(pool, [], window_s=3600)
        assert pct == 0.0

    @pytest.mark.asyncio
    async def test_coverage_db_error_returns_zero(self):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=Exception("pool exhausted"))
        pool = _mock_pool(conn)
        pct = await get_coverage_pct(pool, ["A"], window_s=3600)
        assert pct == 0.0


class TestBatchedRun:
    # / phase 9 batching behavior: wall-clock budget, per-symbol timeout,
    # / min-refresh skip, staleness ordering. uses the public run() API so
    # / backward-compat callers keep working when budget/timeout are None.

    def setup_method(self):
        self.agent = AnalystAgent()

    def _patches_fast_symbol(self):
        # / common patch set where every analysis component succeeds quickly
        return (
            patch("src.agents.analyst_agent.analyze_ratios", new_callable=AsyncMock, return_value=_make_ratio()),
            patch("src.agents.analyst_agent.analyze_dcf", new_callable=AsyncMock, return_value=_make_dcf()),
            patch("src.agents.analyst_agent.analyze_earnings", new_callable=AsyncMock, return_value=_make_earnings()),
            patch("src.agents.analyst_agent.analyze_insider_activity", new_callable=AsyncMock, return_value=_make_insider()),
            patch("src.agents.analyst_agent.generate_dual_analysis", new_callable=AsyncMock, return_value=_make_dual_analysis()),
            patch("src.agents.analyst_agent.store_analysis_score", new_callable=AsyncMock, return_value=1),
        )

    @pytest.mark.asyncio
    async def test_budget_stops_iteration_before_all_symbols(self):
        # / budget exhausted mid-batch → loop exits, remaining symbols deferred
        # / to next cycle. patch _analyze_symbol directly to make timing crisp.
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        pool = _mock_pool(conn)

        async def slow_analyze(pool, symbol):
            await asyncio.sleep(0.1)  # / 100ms per symbol
            return 55.0

        with patch.object(self.agent, "_analyze_symbol", side_effect=slow_analyze):
            results = await self.agent.run(
                pool, ["A", "B", "C", "D", "E"],
                wall_clock_budget_s=0.15,  # / budget ~ 1.5 symbols
                inter_symbol_sleep_s=0.0,
            )

        # / budget tight → fewer than 5 symbols processed, some deferred
        assert len(results) < 5
        assert len(results) >= 1  # / at least one got through

    @pytest.mark.asyncio
    async def test_per_symbol_timeout_does_not_crash_batch(self):
        # / one symbol hangs past the timeout; others still complete. patching
        # / _analyze_symbol directly keeps the test focused on batch-iteration
        # / mechanics (not the 2-second real pipeline per symbol).
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])  # / no prior scores
        pool = _mock_pool(conn)

        async def fake_analyze(pool, symbol):
            if symbol == "HANG":
                await asyncio.sleep(2.0)  # / > per_symbol_timeout_s
                return 99.0
            await asyncio.sleep(0.01)
            return 77.0

        with patch.object(self.agent, "_analyze_symbol", side_effect=fake_analyze):
            results = await self.agent.run(
                pool, ["GOOD1", "HANG", "GOOD2"],
                per_symbol_timeout_s=0.5,
                inter_symbol_sleep_s=0.0,
            )

        assert results.get("HANG") is None
        assert results.get("GOOD1") == 77.0
        assert results.get("GOOD2") == 77.0

    @pytest.mark.asyncio
    async def test_min_refresh_skip_short_circuits_to_empty(self):
        # / when every symbol is too-recent, run() returns {} and _analyze_symbol
        # / is never called — saves LLM quota on symbols that were just scored.
        now = datetime.now(tz=timezone.utc)
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            {"symbol": "A", "last_scored": now - timedelta(minutes=1)},
            {"symbol": "B", "last_scored": now - timedelta(minutes=1)},
        ])
        pool = _mock_pool(conn)

        mock_analyze = AsyncMock(return_value=55.0)
        with patch.object(self.agent, "_analyze_symbol", mock_analyze):
            results = await self.agent.run(
                pool, ["A", "B"],
                min_refresh_interval_s=1800.0,  # / 30 min
                inter_symbol_sleep_s=0.0,
            )

        assert results == {}
        mock_analyze.assert_not_called()

    @pytest.mark.asyncio
    async def test_backward_compat_no_budget_processes_all(self):
        # / default args (wall_clock_budget_s=None) → old behavior, all symbols
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        pool = _mock_pool(conn)

        with patch.object(self.agent, "_analyze_symbol", new_callable=AsyncMock, return_value=55.0):
            results = await self.agent.run(
                pool, ["A", "B", "C"],
                inter_symbol_sleep_s=0.0,
            )

        assert set(results.keys()) == {"A", "B", "C"}

    @pytest.mark.asyncio
    async def test_staleness_ordering_picks_oldest(self):
        # / oldest-scored symbol runs first inside a single cycle
        now = datetime.now(tz=timezone.utc)
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[
            {"symbol": "FRESH", "last_scored": now - timedelta(minutes=5)},
            {"symbol": "STALE", "last_scored": now - timedelta(hours=3)},
        ])
        pool = _mock_pool(conn)

        seen: list[str] = []

        async def record_symbol(pool, symbol):
            seen.append(symbol)
            return 55.0

        with patch.object(self.agent, "_analyze_symbol", side_effect=record_symbol):
            await self.agent.run(
                pool, ["FRESH", "STALE"],
                min_refresh_interval_s=0.0,
                inter_symbol_sleep_s=0.0,
            )

        # / STALE (3h old) ran before FRESH (5 min old)
        assert seen == ["STALE", "FRESH"]

