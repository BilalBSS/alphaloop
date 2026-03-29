# / analyst agent — runs fundamental analysis pipeline per symbol
# / writes composite scores to analysis_scores table
# / graceful: one symbol failure doesn't stop the batch

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any

import structlog

from src.analysis.ratio_analysis import RatioScore, analyze_ratios
from src.analysis.dcf_model import DCFResult, analyze_dcf
from src.analysis.earnings_signals import EarningsSignal, analyze_earnings
from src.analysis.insider_activity import InsiderSignal, analyze_insider_activity
from src.analysis.ai_summary import generate_dual_analysis, generate_summary
from src.agents import tools
from src.data.crypto_data import fetch_coin_data, fetch_funding_rates, get_funding_rate
from src.data.news_sentiment import compute_sentiment_score, store_sentiment
from src.data.social_sentiment import run_social_sentiment
from src.data.symbols import is_crypto
from src.notifications.notifier import notify_analysis_highlight

logger = structlog.get_logger(__name__)


class AnalystAgent:
    # / stateless — all persistent state lives in the database

    def __init__(self):
        self._funding_cache: dict | None = None

    async def run(self, pool, symbols: list[str], run_deepseek: bool = True) -> dict[str, float | None]:
        # / run full analysis pipeline for each symbol
        # / run_deepseek=False: groq only (30-min cycle), True: groq + deepseek (hourly)
        self._run_deepseek = run_deepseek
        self._funding_cache = None  # / reset per cycle
        results: dict[str, float | None] = {}

        # / social sentiment: stocktwits + fear & greed for all symbols
        try:
            await run_social_sentiment(pool, symbols)
        except Exception as exc:
            logger.warning("social_sentiment_batch_failed", error=str(exc))

        for i, symbol in enumerate(symbols):
            try:
                score = await self._analyze_symbol(pool, symbol)
                results[symbol] = score
            except Exception as exc:
                logger.warning("analyst_symbol_failed", symbol=symbol, error=str(exc))
                results[symbol] = None
            # / throttle between symbols to avoid groq 429 rate limits
            if i < len(symbols) - 1:
                await asyncio.sleep(2)

        logger.info("analyst_run_complete", symbols_analyzed=len(results),
                     successful=sum(1 for v in results.values() if v is not None))
        return results

    async def _analyze_symbol(self, pool, symbol: str) -> float | None:
        # / route to crypto or equity analysis path
        if is_crypto(symbol):
            return await self._analyze_crypto_symbol(pool, symbol)
        return await self._analyze_equity_symbol(pool, symbol)

    async def _analyze_crypto_symbol(self, pool, symbol: str) -> float | None:
        # / crypto: NVT from coingecko + sentiment + LLM analysis
        sentiment_score: float | None = None
        try:
            sentiment_score = await compute_sentiment_score(symbol)
            if sentiment_score and sentiment_score != 0.0:
                await store_sentiment(pool, symbol, sentiment_score)
        except Exception as exc:
            logger.warning("analyst_sentiment_failed", symbol=symbol, error=str(exc))

        # / fetch coingecko data for NVT
        nvt: float | None = None
        coin_data: dict | None = None
        try:
            coin_data = await fetch_coin_data(symbol)
            if coin_data:
                mcap = coin_data.get("market_cap")
                vol = coin_data.get("total_volume")
                if mcap and vol and vol > 0:
                    nvt = mcap / vol
        except Exception as exc:
            logger.warning("analyst_crypto_coingecko_failed", symbol=symbol, error=str(exc))

        # / fetch cross-exchange funding rate via loris tools (cached per cycle)
        funding_rate: float | None = None
        oi_rank: int | None = None
        try:
            if self._funding_cache is None:
                self._funding_cache = await fetch_funding_rates() or {}
            if self._funding_cache:
                fr = get_funding_rate(self._funding_cache, symbol)
                if fr:
                    funding_rate = fr["funding_rate"]
                    oi_rank = fr.get("oi_rank")
        except Exception as exc:
            self._funding_cache = {}  # / mark as attempted, don't retry per-symbol
            logger.warning("analyst_crypto_funding_failed", symbol=symbol, error=str(exc))

        regime: str | None = None
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT regime FROM regime_history
                    WHERE market = 'crypto' ORDER BY date DESC LIMIT 1"""
                )
                if row:
                    regime = row["regime"]
        except Exception:
            pass

        # / llm analysis: same dual-llm path as equities
        # / 30-min cycle: groq only, hourly cycle: groq + deepseek
        ai_signal: str | None = None
        ai_summary_text: str | None = None
        crypto_context = regime or "unknown"
        if nvt is not None:
            crypto_context += f" | mcap/vol ratio: {nvt:.1f}"
        if funding_rate is not None:
            crypto_context += f" | funding rate: {funding_rate:+.6f}"
        if coin_data:
            ch24 = coin_data.get("price_change_24h_pct")
            ch7d = coin_data.get("price_change_7d_pct")
            if ch24 is not None:
                crypto_context += f" | 24h: {ch24:+.1f}%"
            if ch7d is not None:
                crypto_context += f" | 7d: {ch7d:+.1f}%"
            mcap = coin_data.get("market_cap")
            if mcap:
                crypto_context += f" | mcap: ${mcap / 1e9:.1f}B"
        if sentiment_score is not None:
            crypto_context += f" | sentiment: {sentiment_score:+.2f}"

        deepseek_text: str | None = None
        if getattr(self, "_run_deepseek", True):
            # / hourly: dual-llm (groq + deepseek), same as equities
            try:
                dual = await generate_dual_analysis(
                    symbol, ratio=None, dcf=None, earnings=None, insider=None, regime=crypto_context,
                )
                ai_signal = dual.consensus
                ai_summary_text = dual.groq.summary if dual.groq else None
                deepseek_text = dual.deepseek.summary if dual.deepseek else None
            except Exception as exc:
                logger.warning("analyst_crypto_dual_failed", symbol=symbol, error=str(exc))
        else:
            # / 30-min: groq only
            try:
                summary = await generate_summary(
                    symbol, ratio=None, dcf=None, earnings=None, insider=None, regime=crypto_context,
                )
                if summary:
                    ai_signal = summary.signal
                    ai_summary_text = summary.summary
            except Exception as exc:
                logger.warning("analyst_crypto_llm_failed", symbol=symbol, error=str(exc))

        # / compute crypto composite: sentiment 0.2, mcap/vol 0.2, funding 0.2, AI 0.4
        components: list[tuple[float, float]] = []
        if sentiment_score is not None and sentiment_score != 0.0:
            sent_100 = max(0.0, min(100.0, (sentiment_score + 1.0) * 50.0))
            components.append((sent_100, 0.2))
        if nvt is not None:
            # / mcap/vol ratio (uses exchange volume, not on-chain tx volume)
            # / typical range 1-20: low = high liquidity (bullish), high = low liquidity
            mvr_score = max(0.0, min(100.0, (15.0 - nvt) / 15.0 * 80.0 + 10.0))
            components.append((mvr_score, 0.2))
        if funding_rate is not None:
            # / funding rate: negative = bullish (shorts paying), positive = bearish
            fr_score = max(0.0, min(100.0, (0.01 - funding_rate) / 0.02 * 100.0))
            components.append((fr_score, 0.2))
        if ai_signal:
            signal_map = {"bullish": 80.0, "neutral": 50.0, "bearish": 20.0}
            components.append((signal_map.get(ai_signal, 50.0), 0.4))

        composite: float | None = None
        if components:
            total_w = sum(w for _, w in components)
            composite = round(sum(s * w for s, w in components) / total_w, 1)

        details: dict = {
            "nvt_ratio": nvt,
            "funding_rate": funding_rate,
            "oi_rank": oi_rank,
            "ai_consensus": ai_signal,
            "news_sentiment_score": sentiment_score,
        }
        if coin_data:
            details["price_change_24h"] = coin_data.get("price_change_24h_pct")
            details["price_change_7d"] = coin_data.get("price_change_7d_pct")
            details["market_cap"] = coin_data.get("market_cap")
        # / use same field names as equity path so dashboard AiAnalysisPanel works
        if ai_summary_text:
            details["llm_analysis_groq"] = ai_summary_text
            details["llm_signal_groq"] = ai_signal
        if deepseek_text:
            details["llm_analysis_deepseek"] = deepseek_text

        await tools.store_analysis_score(
            pool, symbol=symbol, as_of=date.today(),
            fundamental_score=composite, technical_score=None, composite_score=composite,
            regime=regime, regime_confidence=None, used_fundamentals=nvt is not None,
            details=details,
        )

        # / notify discord on strong crypto signals
        if ai_signal in ("bullish", "bearish") and composite is not None:
            notify_details = {
                "nvt_ratio": nvt,
                "regime": regime,
                "ai_excerpt": ai_summary_text[:200] if ai_summary_text else None,
            }
            if coin_data:
                notify_details["price_change_24h"] = coin_data.get("price_change_24h_pct")
            notify_analysis_highlight(symbol, ai_signal, composite, details=notify_details)

        logger.info("analyst_crypto_complete", symbol=symbol, composite=composite, nvt=nvt)
        return composite

    async def _analyze_equity_symbol(self, pool, symbol: str) -> float | None:
        # / run all analysis components, compute composite, store to db
        ratio_score: RatioScore | None = None
        dcf_result: DCFResult | None = None
        earnings_signal: EarningsSignal | None = None
        insider_signal: InsiderSignal | None = None
        regime: str | None = None

        # / each component independently try/excepted
        try:
            ratio_score = await analyze_ratios(pool, symbol)
        except Exception as exc:
            logger.warning("analyst_ratio_failed", symbol=symbol, error=str(exc))

        try:
            dcf_result = await analyze_dcf(pool, symbol)
        except Exception as exc:
            logger.warning("analyst_dcf_failed", symbol=symbol, error=str(exc))

        try:
            earnings_signal = await analyze_earnings(symbol)
        except Exception as exc:
            logger.warning("analyst_earnings_failed", symbol=symbol, error=str(exc))

        # / skip insider analysis for etfs — no form 4 filings
        from src.data.symbols import get_sector
        if get_sector(symbol) != "etfs":
            try:
                insider_signal = await analyze_insider_activity(pool, symbol)
            except Exception as exc:
                logger.warning("analyst_insider_failed", symbol=symbol, error=str(exc))

        # / news sentiment (phase 8)
        sentiment_score: float | None = None
        try:
            sentiment_score = await compute_sentiment_score(symbol)
            if sentiment_score != 0.0:
                await store_sentiment(pool, symbol, sentiment_score)
        except Exception as exc:
            logger.warning("analyst_sentiment_failed", symbol=symbol, error=str(exc))

        # / fetch latest regime from regime_history
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT regime, confidence FROM regime_history
                    WHERE market = 'equity' ORDER BY date DESC LIMIT 1"""
                )
                if row:
                    regime = row["regime"]
        except Exception as exc:
            logger.warning("regime_fetch_failed", symbol=symbol, error=str(exc))

        # / store dcf result to dcf_valuations table (regime known at this point)
        if dcf_result:
            try:
                from src.analysis.dcf_model import store_dcf_result
                await store_dcf_result(pool, dcf_result, regime=regime)
            except Exception as exc:
                logger.warning("analyst_dcf_store_failed", symbol=symbol, error=str(exc))

        # / fetch indicators + sentiment from db for llm prompt enrichment
        indicator_data: dict | None = None
        sentiment_data: dict | None = None
        try:
            async with pool.acquire() as conn:
                ind_row = await conn.fetchrow(
                    """SELECT rsi14, macd_histogram, adx FROM computed_indicators
                    WHERE symbol = $1 ORDER BY date DESC LIMIT 1""",
                    symbol,
                )
                if ind_row:
                    indicator_data = dict(ind_row)
        except Exception:
            pass
        try:
            async with pool.acquire() as conn:
                news_row = await conn.fetchrow(
                    """SELECT sentiment_score FROM news_sentiment
                    WHERE symbol = $1 ORDER BY date DESC LIMIT 1""",
                    symbol,
                )
                social_row = await conn.fetchrow(
                    """SELECT bullish_pct, volume FROM social_sentiment
                    WHERE symbol = $1 ORDER BY date DESC LIMIT 1""",
                    symbol,
                )
                sentiment_data = {}
                if news_row:
                    sentiment_data["news_score"] = news_row["sentiment_score"]
                if social_row:
                    sentiment_data["social"] = dict(social_row)
                if not sentiment_data:
                    sentiment_data = None
        except Exception:
            pass

        # / llm analysis: groq every cycle, deepseek only on hourly cycle
        try:
            if getattr(self, "_run_deepseek", True):
                dual = await generate_dual_analysis(
                    symbol, ratio=ratio_score, dcf=dcf_result,
                    earnings=earnings_signal, insider=insider_signal, regime=regime,
                    indicators=indicator_data, sentiment=sentiment_data,
                )
            else:
                # / groq only, skip deepseek call
                groq_only = await generate_summary(
                    symbol, ratio=ratio_score, dcf=dcf_result,
                    earnings=earnings_signal, insider=insider_signal, regime=regime,
                    indicators=indicator_data, sentiment=sentiment_data,
                )
                from src.analysis.ai_summary import DualAnalysis
                dual = DualAnalysis(groq=groq_only, deepseek=None, consensus=groq_only.signal)
        except Exception as exc:
            logger.warning("analyst_llm_failed", symbol=symbol, error=str(exc))
            from src.analysis.ai_summary import DualAnalysis, _build_fallback_summary
            fallback = _build_fallback_summary(symbol, ratio_score, dcf_result, earnings_signal, insider_signal)
            dual = DualAnalysis(groq=fallback, deepseek=None, consensus=fallback.signal)

        # / compute fundamental score as weighted average of available components
        fundamental_score = self._compute_fundamental_score(
            ratio_score, dcf_result, earnings_signal, insider_signal,
        )

        # / build details dict for JSONB storage
        details = self._build_details(
            ratio_score, dcf_result, earnings_signal, insider_signal, dual.groq,
        )
        # / store individual 0-100 component scores for dashboard breakdown
        if ratio_score and ratio_score.composite_score is not None:
            details["ratio_score_100"] = ratio_score.composite_score
        if dcf_result and dcf_result.upside_pct is not None:
            details["dcf_score_100"] = round(max(0.0, min(100.0, (dcf_result.upside_pct + 0.5) / 1.0 * 100)), 1)
        if earnings_signal and earnings_signal.strength is not None:
            details["earnings_score_100"] = earnings_signal.strength
        if insider_signal and insider_signal.strength is not None:
            details["insider_score_100"] = insider_signal.strength
        # / add dual-llm fields
        details["ai_consensus"] = dual.consensus
        details["llm_analysis_groq"] = dual.groq.summary
        details["llm_signal_groq"] = dual.groq.signal
        if dual.deepseek:
            details["llm_analysis_deepseek"] = dual.deepseek.summary
            details["llm_signal_deepseek"] = dual.deepseek.signal

        # / store to analysis_scores
        used_fundamentals = ratio_score is not None or dcf_result is not None
        await tools.store_analysis_score(
            pool, symbol=symbol, as_of=date.today(),
            fundamental_score=fundamental_score,
            technical_score=None,  # / populated by strategy agent
            composite_score=fundamental_score,  # / same as fundamental until technical added
            regime=regime,
            regime_confidence=None,
            used_fundamentals=used_fundamentals,
            details=details,
        )

        logger.info("analyst_symbol_complete", symbol=symbol, score=fundamental_score)

        # / notify discord on strong consensus
        if dual.consensus in ("bullish", "bearish") and fundamental_score is not None:
            notify_details = {
                "pe_ratio": details.get("pe_ratio"),
                "dcf_upside": details.get("dcf_upside"),
                "earnings_surprise_pct": details.get("earnings_surprise_pct"),
                "consecutive_beats": details.get("consecutive_beats"),
                "insider_signal": details.get("insider_signal"),
                "regime": regime,
                "ai_excerpt": dual.groq.summary if dual.groq and hasattr(dual.groq, "summary") else None,
            }
            notify_analysis_highlight(symbol, dual.consensus, fundamental_score, details=notify_details)

        return fundamental_score

    def _compute_fundamental_score(
        self,
        ratio: RatioScore | None,
        dcf: DCFResult | None,
        earnings: EarningsSignal | None,
        insider: InsiderSignal | None,
    ) -> float | None:
        # / weighted average of available analysis components
        # / weights: ratio 0.35, dcf 0.25, earnings 0.20, insider 0.20
        components: list[tuple[float, float]] = []  # (score, weight)

        if ratio and ratio.composite_score is not None:
            components.append((ratio.composite_score, 0.35))

        if dcf and dcf.upside_pct is not None:
            # / normalize upside to 0-100 scale: -50% -> 0, +50% -> 100
            dcf_score = max(0.0, min(100.0, (dcf.upside_pct + 0.5) / 1.0 * 100))
            components.append((dcf_score, 0.25))

        if earnings and earnings.strength is not None:
            components.append((earnings.strength, 0.20))

        if insider and insider.strength is not None:
            components.append((insider.strength, 0.20))

        if not components:
            return None

        total_weight = sum(w for _, w in components)
        weighted_sum = sum(s * w for s, w in components)
        return round(weighted_sum / total_weight, 1)

    def _build_details(
        self,
        ratio: RatioScore | None,
        dcf: DCFResult | None,
        earnings: EarningsSignal | None,
        insider: InsiderSignal | None,
        summary: Any,
    ) -> dict:
        # / build jsonb details for strategy agent to reconstruct AnalysisData
        d: dict[str, Any] = {}
        if ratio:
            d["pe_ratio"] = float(ratio.details.get("pe_ratio")) if ratio.details.get("pe_ratio") else None
            d["ps_ratio"] = float(ratio.details.get("ps_ratio")) if ratio.details.get("ps_ratio") else None
            d["peg_ratio"] = float(ratio.details.get("peg_ratio")) if ratio.details.get("peg_ratio") else None
            d["fcf_margin"] = float(ratio.details.get("fcf_margin")) if ratio.details.get("fcf_margin") else None
            d["debt_to_equity"] = float(ratio.details.get("debt_to_equity")) if ratio.details.get("debt_to_equity") else None
            d["ratio_composite"] = ratio.composite_score
        if dcf:
            d["dcf_upside"] = dcf.upside_pct
            d["dcf_median"] = dcf.fair_value_median
            d["dcf_confidence"] = dcf.confidence
        if earnings:
            d["earnings_surprise_pct"] = earnings.surprise_pct
            d["consecutive_beats"] = earnings.consecutive_beats
            d["earnings_signal"] = earnings.signal
        if insider:
            d["insider_net_buy_ratio"] = insider.net_buy_ratio
            d["insider_signal"] = insider.signal
        if summary:
            d["summary"] = summary.summary if hasattr(summary, "summary") else str(summary)
            d["summary_signal"] = summary.signal if hasattr(summary, "signal") else None
        # / sentiment_score is stored directly to news_sentiment table,
        # / but also include in details for strategy agent consumption
        return d
