# / tests for insider activity analysis

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.analysis.insider_activity import (
    _detect_cluster,
    _title_weight,
    analyze_insider_activity,
    compute_insider_signal,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestTitleWeight:
    def test_ceo_highest(self):
        assert _title_weight("CEO") == 3.0

    def test_cfo(self):
        assert _title_weight("Chief Financial Officer") == 2.5

    def test_director(self):
        assert _title_weight("Director") == 1.0

    def test_vp(self):
        assert _title_weight("VP of Engineering") == 1.5

    def test_vice_president(self):
        assert _title_weight("Vice President") == 1.5

    def test_unknown(self):
        assert _title_weight("Board Member") == 1.0

    def test_empty(self):
        assert _title_weight("") == 1.0

    def test_case_insensitive(self):
        assert _title_weight("ceo") == 3.0


class TestDetectCluster:
    def test_cluster_detected(self):
        base_date = date(2026, 3, 1)
        trades = [
            {"transaction_type": "buy", "filing_date": base_date, "insider_name": "Alice"},
            {"transaction_type": "buy", "filing_date": base_date + timedelta(days=5), "insider_name": "Bob"},
            {"transaction_type": "buy", "filing_date": base_date + timedelta(days=10), "insider_name": "Charlie"},
        ]
        assert _detect_cluster(trades) is True

    def test_no_cluster_too_few(self):
        base_date = date(2026, 3, 1)
        trades = [
            {"transaction_type": "buy", "filing_date": base_date, "insider_name": "Alice"},
            {"transaction_type": "buy", "filing_date": base_date + timedelta(days=5), "insider_name": "Bob"},
        ]
        assert _detect_cluster(trades) is False

    def test_no_cluster_too_spread(self):
        trades = [
            {"transaction_type": "buy", "filing_date": date(2026, 1, 1), "insider_name": "Alice"},
            {"transaction_type": "buy", "filing_date": date(2026, 2, 15), "insider_name": "Bob"},
            {"transaction_type": "buy", "filing_date": date(2026, 3, 25), "insider_name": "Charlie"},
        ]
        assert _detect_cluster(trades) is False

    def test_sells_excluded(self):
        base_date = date(2026, 3, 1)
        trades = [
            {"transaction_type": "sell", "filing_date": base_date, "insider_name": "Alice"},
            {"transaction_type": "sell", "filing_date": base_date + timedelta(days=5), "insider_name": "Bob"},
            {"transaction_type": "sell", "filing_date": base_date + timedelta(days=10), "insider_name": "Charlie"},
        ]
        assert _detect_cluster(trades) is False


class TestTitleWeightDeep:
    def test_chief_technology_officer(self):
        # / "chief technology" matched at weight 2.0
        assert _title_weight("Chief Technology Officer") == 2.0

    def test_president_not_confused_with_vp(self):
        # / "President" should match "president" at 2.0, not "vice president"
        assert _title_weight("President") == 2.0

    def test_cto_after_director_check(self):
        # / "director" contains "cto" substring — "Director of Technology"
        # / should match "director" first at 1.0
        assert _title_weight("Director of Technology") == 1.0

    def test_director_matches_before_cto(self):
        # / ordering: "director" is checked before "cto" in the list
        assert _title_weight("director") == 1.0


class TestDetectClusterDeep:
    def test_cluster_exactly_3_same_day(self):
        # / exactly 3 insiders on the same day
        d = date(2026, 3, 15)
        trades = [
            {"transaction_type": "buy", "filing_date": d, "insider_name": "A"},
            {"transaction_type": "buy", "filing_date": d, "insider_name": "B"},
            {"transaction_type": "buy", "filing_date": d, "insider_name": "C"},
        ]
        assert _detect_cluster(trades) is True

    def test_4_insiders_within_29_days(self):
        # / 4 insiders spread over 29 days — within 30-day window
        base = date(2026, 3, 1)
        trades = [
            {"transaction_type": "buy", "filing_date": base, "insider_name": "A"},
            {"transaction_type": "buy", "filing_date": base + timedelta(days=10), "insider_name": "B"},
            {"transaction_type": "buy", "filing_date": base + timedelta(days=20), "insider_name": "C"},
            {"transaction_type": "buy", "filing_date": base + timedelta(days=29), "insider_name": "D"},
        ]
        assert _detect_cluster(trades) is True

    def test_3_insiders_exactly_30_days_boundary(self):
        # / 3 insiders: first on day 0, last on day 30
        # / window check: window_start = trade_date - 30 days
        # / for the last trade (day 30): window_start = day 0, so day 0 is included
        base = date(2026, 3, 1)
        trades = [
            {"transaction_type": "buy", "filing_date": base, "insider_name": "A"},
            {"transaction_type": "buy", "filing_date": base + timedelta(days=15), "insider_name": "B"},
            {"transaction_type": "buy", "filing_date": base + timedelta(days=30), "insider_name": "C"},
        ]
        assert _detect_cluster(trades) is True


class TestComputeInsiderSignal:
    def test_strong_buying(self):
        trades = [
            {"transaction_type": "buy", "total_value": 500000, "insider_title": "CEO",
             "insider_name": "Alice", "filing_date": date(2026, 3, 1)},
            {"transaction_type": "buy", "total_value": 200000, "insider_title": "CFO",
             "insider_name": "Bob", "filing_date": date(2026, 3, 5)},
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "VP",
             "insider_name": "Charlie", "filing_date": date(2026, 3, 10)},
        ]
        result = compute_insider_signal(trades, "AAPL")
        assert result.signal == "bullish"
        assert result.net_buy_ratio == 1.0
        assert result.total_buys == 3
        assert result.total_sells == 0
        assert result.cluster_detected is True
        assert result.unique_buyers == 3

    def test_strong_selling(self):
        trades = [
            {"transaction_type": "sell", "total_value": 1000000, "insider_title": "CEO",
             "insider_name": "Alice", "filing_date": date(2026, 3, 1)},
            {"transaction_type": "sell", "total_value": 500000, "insider_title": "Director",
             "insider_name": "Bob", "filing_date": date(2026, 3, 5)},
        ]
        result = compute_insider_signal(trades, "AAPL")
        assert result.signal == "bearish"
        assert result.net_buy_ratio == -1.0
        assert result.total_sells == 2

    def test_mixed_activity(self):
        trades = [
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "Director",
             "insider_name": "Alice", "filing_date": date(2026, 3, 1)},
            {"transaction_type": "sell", "total_value": 100000, "insider_title": "Director",
             "insider_name": "Bob", "filing_date": date(2026, 3, 5)},
        ]
        result = compute_insider_signal(trades, "AAPL")
        assert result.net_buy_ratio == 0.0
        assert result.signal == "neutral"

    def test_no_trades(self):
        result = compute_insider_signal([], "AAPL")
        assert result.signal == "neutral"
        assert result.strength == 0.0
        assert result.total_buys == 0

    def test_option_exercises_excluded(self):
        trades = [
            {"transaction_type": "option_exercise", "total_value": 5000000,
             "insider_title": "CEO", "insider_name": "Alice", "filing_date": date(2026, 3, 1)},
            {"transaction_type": "buy", "total_value": 50000,
             "insider_title": "Director", "insider_name": "Bob", "filing_date": date(2026, 3, 5)},
        ]
        result = compute_insider_signal(trades, "AAPL")
        # / option exercise shouldn't count as buy or sell
        assert result.total_buys == 1
        assert result.total_sells == 0

    def test_officer_weight_matters(self):
        # / ceo buy should produce stronger signal than director buy
        ceo_trades = [
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "CEO",
             "insider_name": "Alice", "filing_date": date(2026, 3, 1)},
        ]
        dir_trades = [
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "Director",
             "insider_name": "Bob", "filing_date": date(2026, 3, 1)},
        ]
        ceo_result = compute_insider_signal(ceo_trades, "AAPL")
        dir_result = compute_insider_signal(dir_trades, "AAPL")
        # / both bullish, but same net ratio since no opposing trades
        assert ceo_result.signal == "bullish"
        assert dir_result.signal == "bullish"


class TestComputeInsiderSignalDeep:
    def test_net_buy_ratio_formula(self):
        # / CEO buys 300k (weight 3.0), Director sells 100k (weight 1.0)
        # / weighted_buy = 300000*3 = 900000, weighted_sell = 100000*1 = 100000
        # / ratio = (900000-100000)/(900000+100000) = 800000/1000000 = 0.8
        trades = [
            {"transaction_type": "buy", "total_value": 300000, "insider_title": "CEO",
             "insider_name": "Alice", "filing_date": date(2026, 3, 1)},
            {"transaction_type": "sell", "total_value": 100000, "insider_title": "Director",
             "insider_name": "Bob", "filing_date": date(2026, 3, 5)},
        ]
        result = compute_insider_signal(trades, "CALC")
        assert result.net_buy_ratio == 0.8

    def test_strength_components_add_correctly(self):
        # / ratio_points = |ratio| * 50
        # / cluster_bonus = 25 if cluster
        # / activity_points = min(25, trade_count * 3)
        # / 3 buys from different insiders within 30 days (cluster=True)
        trades = [
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "CEO",
             "insider_name": "A", "filing_date": date(2026, 3, 1)},
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "CFO",
             "insider_name": "B", "filing_date": date(2026, 3, 5)},
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "Director",
             "insider_name": "C", "filing_date": date(2026, 3, 10)},
        ]
        result = compute_insider_signal(trades, "STR")
        # / net_buy_ratio = 1.0 (all buys)
        # / ratio_points = 1.0 * 50 = 50
        # / cluster = True -> +25
        # / activity_points = min(25, 3*3) = 9
        # / total = 50 + 25 + 9 = 84
        assert result.net_buy_ratio == 1.0
        assert result.cluster_detected is True
        assert result.strength == 84.0


class TestAnalyzeInsiderActivity:
    @pytest.mark.asyncio
    async def test_fetches_and_analyzes(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"symbol": "AAPL", "filing_date": date(2026, 3, 1),
             "insider_name": "Tim Cook", "insider_title": "CEO",
             "transaction_type": "buy", "shares": Decimal("1000"),
             "price_per_share": Decimal("180.00"), "total_value": Decimal("180000")},
        ]
        pool = _mock_pool(mock_conn)

        result = await analyze_insider_activity(pool, "AAPL")
        assert result.symbol == "AAPL"
        assert result.total_buys == 1
        assert result.signal == "bullish"

    @pytest.mark.asyncio
    async def test_empty_trades(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        result = await analyze_insider_activity(pool, "AAPL")
        assert result.signal == "neutral"
        assert result.total_buys == 0


class TestSignedStrength:
    # / bug b: insider scorecard was showing magnitude (75) for bearish (-75) signals
    # / fix: add signed_strength = strength * sign(net_buy_ratio), ui reads directly
    def test_bullish_signed_equals_positive_strength(self):
        trades = [
            {"transaction_type": "buy", "total_value": 500000, "insider_title": "CEO",
             "insider_name": "Alice", "filing_date": date(2026, 3, 1)},
            {"transaction_type": "buy", "total_value": 200000, "insider_title": "CFO",
             "insider_name": "Bob", "filing_date": date(2026, 3, 5)},
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "VP",
             "insider_name": "Charlie", "filing_date": date(2026, 3, 10)},
        ]
        result = compute_insider_signal(trades, "AAPL")
        assert result.signal == "bullish"
        assert result.net_buy_ratio > 0
        assert result.signed_strength == result.strength
        assert result.signed_strength > 0

    def test_bearish_signed_equals_negative_strength(self):
        # / regression scenario for amzn/nvda: heavy selling, no buys
        trades = [
            {"transaction_type": "sell", "total_value": 1000000, "insider_title": "CEO",
             "insider_name": "Alice", "filing_date": date(2026, 3, 1)},
            {"transaction_type": "sell", "total_value": 500000, "insider_title": "CFO",
             "insider_name": "Bob", "filing_date": date(2026, 3, 5)},
            {"transaction_type": "sell", "total_value": 300000, "insider_title": "Director",
             "insider_name": "Charlie", "filing_date": date(2026, 3, 10)},
        ]
        result = compute_insider_signal(trades, "AMZN")
        assert result.signal == "bearish"
        assert result.net_buy_ratio < 0
        assert result.signed_strength == -result.strength
        assert result.signed_strength < 0

    def test_empty_trades_signed_is_zero(self):
        result = compute_insider_signal([], "AAPL")
        assert result.strength == 0.0
        assert result.signed_strength == 0.0

    def test_net_neutral_signed_is_zero(self):
        # / equal buy and sell weighted value -> net_buy_ratio = 0 -> signed = 0
        trades = [
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "Director",
             "insider_name": "Alice", "filing_date": date(2026, 3, 1)},
            {"transaction_type": "sell", "total_value": 100000, "insider_title": "Director",
             "insider_name": "Bob", "filing_date": date(2026, 3, 5)},
        ]
        result = compute_insider_signal(trades, "AAPL")
        assert result.net_buy_ratio == 0.0
        assert result.signed_strength == 0.0

    def test_cluster_bullish_signed_positive_with_bonus(self):
        # / cluster + all buys -> strength includes +25 cluster bonus, signed = +strength
        trades = [
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "CEO",
             "insider_name": "A", "filing_date": date(2026, 3, 1)},
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "CFO",
             "insider_name": "B", "filing_date": date(2026, 3, 5)},
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "Director",
             "insider_name": "C", "filing_date": date(2026, 3, 10)},
        ]
        result = compute_insider_signal(trades, "AAPL")
        assert result.cluster_detected is True
        assert result.signed_strength == result.strength
        # / 50 (ratio) + 25 (cluster) + 9 (activity) = 84
        assert result.signed_strength == 84.0

    def test_cluster_bearish_no_cluster_bonus_but_still_negative(self):
        # / clusters are buy-only; 3 sells = no cluster bonus, but signed still negative
        trades = [
            {"transaction_type": "sell", "total_value": 100000, "insider_title": "CEO",
             "insider_name": "A", "filing_date": date(2026, 3, 1)},
            {"transaction_type": "sell", "total_value": 100000, "insider_title": "CFO",
             "insider_name": "B", "filing_date": date(2026, 3, 5)},
            {"transaction_type": "sell", "total_value": 100000, "insider_title": "Director",
             "insider_name": "C", "filing_date": date(2026, 3, 10)},
        ]
        result = compute_insider_signal(trades, "NVDA")
        assert result.cluster_detected is False
        assert result.net_buy_ratio == -1.0
        assert result.signed_strength < 0
        assert result.signed_strength == -result.strength

    def test_strength_unchanged_backwards_compat(self):
        # / regression: existing strength formula unchanged after adding signed field
        # / 3 buying insiders within 30 days: ratio_points=50 + cluster=25 + activity=9 = 84
        trades = [
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "CEO",
             "insider_name": "A", "filing_date": date(2026, 3, 1)},
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "CFO",
             "insider_name": "B", "filing_date": date(2026, 3, 5)},
            {"transaction_type": "buy", "total_value": 100000, "insider_title": "Director",
             "insider_name": "C", "filing_date": date(2026, 3, 10)},
        ]
        result = compute_insider_signal(trades, "REG")
        assert result.strength == 84.0
        assert result.signed_strength == 84.0
