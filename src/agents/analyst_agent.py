# / analyst agent — runs fundamental analysis pipeline per symbol
# / writes composite scores to analysis_scores table
# / graceful: one symbol failure doesn't stop the batch

from __future__ import annotations

import os
from datetime import date
from typing import Any

import structlog

from src.analysis.ratio_analysis import RatioScore, analyze_ratios
from src.analysis.dcf_model import DCFResult, analyze_dcf
from src.analysis.earnings_signals import EarningsSignal, analyze_earnings
from src.analysis.insider_activity import InsiderSignal, analyze_insider_activity
from src.analysis.ai_summary import generate_summary
from src.agents import tools

logger = structlog.get_logger(__name__)


class AnalystAgent:
    # / stateless — all persistent state lives in the database

    async def run(self, pool, symbols: list[str]) -> dict[str, float | None]:
        # / run full analysis pipeline for each symbol
        # / returns {symbol: composite_score} for logging
        results: dict[str, float | None] = {}

        for symbol in symbols:
            try:
                score = await self._analyze_symbol(pool, symbol)
                results[symbol] = score
            except Exception as exc:
                logger.warning("analyst_symbol_failed", symbol=symbol, error=str(exc))
                results[symbol] = None

        logger.info("analyst_run_complete", symbols_analyzed=len(results),
                     successful=sum(1 for v in results.values() if v is not None))
        return results

    async def _analyze_symbol(self, pool, symbol: str) -> float | None:
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

        try:
            insider_signal = await analyze_insider_activity(pool, symbol)
        except Exception as exc:
            logger.warning("analyst_insider_failed", symbol=symbol, error=str(exc))

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

        # / generate ai summary (uses groq or fallback)
        summary = await generate_summary(
            symbol, ratio=ratio_score, dcf=dcf_result,
            earnings=earnings_signal, insider=insider_signal, regime=regime,
        )

        # / compute fundamental score as weighted average of available components
        fundamental_score = self._compute_fundamental_score(
            ratio_score, dcf_result, earnings_signal, insider_signal,
        )

        # / build details dict for JSONB storage
        details = self._build_details(
            ratio_score, dcf_result, earnings_signal, insider_signal, summary,
        )

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
        return d
