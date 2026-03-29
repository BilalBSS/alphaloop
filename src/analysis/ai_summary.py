# / ai-powered analysis summary using groq free tier
# / takes all analysis results for a symbol and produces natural language summary
# / graceful: returns formatted fallback if groq unavailable

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog

from .dcf_model import DCFResult
from .earnings_signals import EarningsSignal
from .insider_activity import InsiderSignal
from .ratio_analysis import RatioScore

logger = structlog.get_logger(__name__)

# / groq free tier: separate rate limit pools per model
DEFAULT_MODEL = "llama-3.1-8b-instant"
FALLBACK_MODEL = "openai/gpt-oss-20b"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_BASE = "https://api.deepseek.com/v1"
MAX_TOKENS = 500


@dataclass
class AnalysisSummary:
    symbol: str
    date: date
    summary: str
    model_used: str | None  # none if fallback was used
    signal: str             # bullish, bearish, neutral
    confidence: float       # 0-100


@dataclass
class DualAnalysis:
    groq: AnalysisSummary
    deepseek: AnalysisSummary | None
    consensus: str  # bullish, bearish, neutral, disagree


def _build_prompt(
    symbol: str,
    ratio: RatioScore | None,
    dcf: DCFResult | None,
    earnings: EarningsSignal | None,
    insider: InsiderSignal | None,
    regime: str | None = None,
    indicators: dict | None = None,
    sentiment: dict | None = None,
) -> str:
    # / construct analysis prompt from available data
    parts = [f"Provide a concise investment analysis summary for {symbol}."]
    parts.append("Be direct. State the signal (bullish/bearish/neutral) first, then key reasons.")

    if regime:
        parts.append(f"\nMarket regime: {regime}")

    if ratio:
        parts.append(f"\nValuation ratios (score 0-100, higher=more undervalued):")
        parts.append(f"  Composite: {ratio.composite_score}")
        if ratio.pe_score is not None:
            parts.append(f"  P/E score: {ratio.pe_score}")
        if ratio.peg_score is not None:
            parts.append(f"  PEG score: {ratio.peg_score}")
        if ratio.fcf_margin_score is not None:
            parts.append(f"  FCF margin score: {ratio.fcf_margin_score}")

    if dcf:
        parts.append(f"\nDCF valuation:")
        parts.append(f"  Fair value (median): ${dcf.fair_value_median:.2f}")
        parts.append(f"  Current price: ${dcf.current_price:.2f}")
        parts.append(f"  Upside: {dcf.upside_pct:.1%}")
        parts.append(f"  Range: ${dcf.fair_value_p10:.2f} (bear) to ${dcf.fair_value_p90:.2f} (bull)")
        parts.append(f"  Confidence: {dcf.confidence}")

    if earnings:
        parts.append(f"\nEarnings signals:")
        parts.append(f"  Signal: {earnings.signal} (strength: {earnings.strength})")
        if earnings.surprise_pct is not None:
            parts.append(f"  Latest surprise: {earnings.surprise_pct:.1%}")
        parts.append(f"  Consecutive beats: {earnings.consecutive_beats}")

    if insider:
        parts.append(f"\nInsider activity:")
        parts.append(f"  Signal: {insider.signal} (strength: {insider.strength})")
        parts.append(f"  Net buy ratio: {insider.net_buy_ratio:.2f}")
        parts.append(f"  Buys: {insider.total_buys}, Sells: {insider.total_sells}")
        if insider.cluster_detected:
            parts.append(f"  Cluster buying detected")

    if indicators:
        parts.append(f"\nTechnical indicators:")
        rsi = indicators.get("rsi14")
        if rsi is not None:
            label = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
            parts.append(f"  RSI(14): {rsi:.1f} ({label})")
        macd_h = indicators.get("macd_histogram")
        if macd_h is not None:
            parts.append(f"  MACD histogram: {macd_h:.4f} ({'bullish' if macd_h > 0 else 'bearish'})")
        adx = indicators.get("adx")
        if adx is not None:
            parts.append(f"  ADX: {adx:.1f} ({'strong' if adx > 25 else 'weak'} trend)")

    if sentiment:
        parts.append(f"\nSentiment:")
        news_score = sentiment.get("news_score")
        if news_score is not None:
            label = "bullish" if news_score > 0.1 else "bearish" if news_score < -0.1 else "neutral"
            parts.append(f"  News: {news_score:.2f} ({label})")
        social = sentiment.get("social")
        if social:
            vol = social.get("volume", 0)
            bull = social.get("bullish_pct")
            if vol or bull is not None:
                s = f"  Social: {vol} mentions" if vol else "  Social:"
                if bull is not None:
                    s += f", {bull:.0%} bullish"
                parts.append(s)

    parts.append("\nKeep the summary under 150 words. Focus on actionable insight.")

    return "\n".join(parts)


def _build_fallback_summary(
    symbol: str,
    ratio: RatioScore | None,
    dcf: DCFResult | None,
    earnings: EarningsSignal | None,
    insider: InsiderSignal | None,
) -> AnalysisSummary:
    # / structured fallback when groq is unavailable
    signals: list[str] = []
    bullish_count = 0
    bearish_count = 0
    total_strength = 0.0
    signal_count = 0

    if ratio and ratio.composite_score is not None:
        if ratio.composite_score >= 60:
            signals.append(f"Valuation: undervalued (score {ratio.composite_score})")
            bullish_count += 1
        elif ratio.composite_score <= 40:
            signals.append(f"Valuation: overvalued (score {ratio.composite_score})")
            bearish_count += 1
        else:
            signals.append(f"Valuation: fair (score {ratio.composite_score})")
        total_strength += ratio.composite_score
        signal_count += 1

    if dcf:
        if dcf.upside_pct > 0.10:
            signals.append(f"DCF: {dcf.upside_pct:.0%} upside to ${dcf.fair_value_median:.2f} ({dcf.confidence} confidence)")
            bullish_count += 1
        elif dcf.upside_pct < -0.10:
            signals.append(f"DCF: {dcf.upside_pct:.0%} downside from ${dcf.current_price:.2f} ({dcf.confidence} confidence)")
            bearish_count += 1
        else:
            signals.append(f"DCF: fairly valued at ${dcf.fair_value_median:.2f}")
        # / dcf contributes to confidence via upside magnitude
        total_strength += min(100.0, abs(dcf.upside_pct) * 200)
        signal_count += 1

    if earnings:
        if earnings.signal == "bullish":
            signals.append(f"Earnings: bullish (strength {earnings.strength}, {earnings.consecutive_beats} consecutive beats)")
            bullish_count += 1
        elif earnings.signal == "bearish":
            signals.append(f"Earnings: bearish (strength {earnings.strength})")
            bearish_count += 1
        total_strength += earnings.strength
        signal_count += 1

    if insider:
        if insider.signal == "bullish":
            signals.append(f"Insiders: net buying ({insider.total_buys} buys vs {insider.total_sells} sells)")
            bullish_count += 1
        elif insider.signal == "bearish":
            signals.append(f"Insiders: net selling ({insider.total_sells} sells vs {insider.total_buys} buys)")
            bearish_count += 1
        if insider.cluster_detected:
            signals.append("  Insider cluster buying detected — strong bullish signal")
        total_strength += insider.strength
        signal_count += 1

    # / overall signal
    if bullish_count > bearish_count:
        overall = "bullish"
    elif bearish_count > bullish_count:
        overall = "bearish"
    else:
        overall = "neutral"

    avg_strength = total_strength / signal_count if signal_count > 0 else 0.0
    summary = f"{symbol} — {overall.upper()}\n" + "\n".join(f"• {s}" for s in signals)

    return AnalysisSummary(
        symbol=symbol,
        date=date.today(),
        summary=summary,
        model_used=None,
        signal=overall,
        confidence=round(avg_strength, 1),
    )


class _RateLimited(Exception):
    pass


async def _call_llm(
    api_key: str, model: str, prompt: str, symbol: str,
) -> AnalysisSummary | None:
    # / single llm api call — returns None on failure, raises _RateLimited on 429
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a concise equity analyst. Give actionable signals."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": MAX_TOKENS,
                    "temperature": 0.3,
                },
            )
            if resp.status_code == 429:
                logger.info("llm_rate_limited", symbol=symbol, model=model)
                raise _RateLimited()
            resp.raise_for_status()
            data = resp.json()
            summary_text = data["choices"][0]["message"]["content"].strip()
            lower = summary_text.lower()
            signal = "bullish" if "bullish" in lower[:50] else "bearish" if "bearish" in lower[:50] else "neutral"
            logger.info("ai_summary_generated", symbol=symbol, model=model)
            return AnalysisSummary(
                symbol=symbol, date=date.today(), summary=summary_text,
                model_used=model, signal=signal, confidence=75.0,
            )
    except _RateLimited:
        raise
    except Exception as exc:
        logger.info("llm_model_failed", symbol=symbol, model=model, error=str(exc)[:100])
        return None


async def generate_summary(
    symbol: str,
    ratio: RatioScore | None = None,
    dcf: DCFResult | None = None,
    earnings: EarningsSignal | None = None,
    insider: InsiderSignal | None = None,
    regime: str | None = None,
    indicators: dict | None = None,
    sentiment: dict | None = None,
) -> AnalysisSummary:
    # / try groq llm, fall back to structured summary
    prompt = _build_prompt(symbol, ratio, dcf, earnings, insider, regime,
                           indicators=indicators, sentiment=sentiment)

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.info("groq_api_key_missing_using_fallback", symbol=symbol)
        return _build_fallback_summary(symbol, ratio, dcf, earnings, insider)

    # / try models in order: default → 120b → fallback 20b
    # / stop on 429 — all groq models share the same rate limit
    models = [DEFAULT_MODEL, "openai/gpt-oss-120b", FALLBACK_MODEL]
    for model in models:
        try:
            result = await _call_llm(api_key, model, prompt, symbol)
            if result:
                return result
        except _RateLimited:
            break

    logger.warning("all_llm_models_failed_using_fallback", symbol=symbol)
    return _build_fallback_summary(symbol, ratio, dcf, earnings, insider)


async def _generate_deepseek_summary(
    symbol: str,
    ratio: RatioScore | None = None,
    dcf: DCFResult | None = None,
    earnings: EarningsSignal | None = None,
    insider: InsiderSignal | None = None,
    regime: str | None = None,
    indicators: dict | None = None,
    sentiment: dict | None = None,
) -> AnalysisSummary | None:
    # / independent second opinion via deepseek — gets same raw data, NOT groq's output
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    prompt = _build_prompt(symbol, ratio, dcf, earnings, insider, regime,
                           indicators=indicators, sentiment=sentiment)

    try:
        import httpx
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{DEEPSEEK_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are a concise equity analyst. Give actionable signals."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": MAX_TOKENS,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            summary_text = data["choices"][0]["message"]["content"].strip()
            lower = summary_text.lower()
            signal = "bullish" if "bullish" in lower[:50] else "bearish" if "bearish" in lower[:50] else "neutral"
            logger.info("deepseek_summary_generated", symbol=symbol)
            return AnalysisSummary(
                symbol=symbol, date=date.today(), summary=summary_text,
                model_used=DEEPSEEK_MODEL, signal=signal, confidence=75.0,
            )
    except Exception as exc:
        logger.warning("deepseek_api_failed", symbol=symbol, error=str(exc))
        return None


def _compute_consensus(groq_signal: str, deepseek_signal: str | None) -> str:
    # / compute ai consensus from two independent analyses
    if deepseek_signal is None:
        return groq_signal
    if groq_signal == deepseek_signal:
        return groq_signal
    return "disagree"


async def generate_dual_analysis(
    symbol: str,
    ratio: RatioScore | None = None,
    dcf: DCFResult | None = None,
    earnings: EarningsSignal | None = None,
    insider: InsiderSignal | None = None,
    regime: str | None = None,
    indicators: dict | None = None,
    sentiment: dict | None = None,
) -> DualAnalysis:
    # / run groq + deepseek in parallel, compute consensus
    import asyncio
    groq_task = generate_summary(symbol, ratio, dcf, earnings, insider, regime,
                                 indicators=indicators, sentiment=sentiment)
    deepseek_task = _generate_deepseek_summary(symbol, ratio, dcf, earnings, insider, regime,
                                                indicators=indicators, sentiment=sentiment)

    groq_result, deepseek_result = await asyncio.gather(groq_task, deepseek_task)

    consensus = _compute_consensus(
        groq_result.signal,
        deepseek_result.signal if deepseek_result else None,
    )

    logger.info(
        "dual_analysis_complete", symbol=symbol,
        groq=groq_result.signal, deepseek=deepseek_result.signal if deepseek_result else "unavailable",
        consensus=consensus,
    )

    return DualAnalysis(groq=groq_result, deepseek=deepseek_result, consensus=consensus)


async def generate_daily_synthesis(
    pool: Any,
    symbols: list[str],
) -> dict[str, Any] | None:
    # / 5PM ET portfolio-wide synthesis via deepseek-reasoner
    # / reads all today's data, produces top buys/avoids/risk assessment
    import json
    from datetime import date as dt_date
    from src.agents.tools import store_daily_synthesis

    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if not deepseek_key:
        logger.info("no_deepseek_key_skipping_synthesis")
        return None

    # / gather today's analysis scores
    today = dt_date.today()
    scores = []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT symbol, composite_score, regime,
                    details->>'ai_consensus' as ai_consensus
                FROM analysis_scores
                WHERE date >= $1
                ORDER BY composite_score DESC""",
                today,
            )
            scores = [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("synthesis_fetch_scores_failed", error=str(exc))

    if not scores:
        logger.info("synthesis_no_data_today")
        return None

    # / build the prompt
    score_text = "\n".join(
        f"  {s.get('symbol', '?')}: score={s.get('composite_score', 0)}, "
        f"consensus={s.get('ai_consensus', '--')}, regime={s.get('regime', '--')}"
        for s in scores[:50]
    )

    prompt = f"""You are a senior portfolio analyst. Review today's data across all symbols and produce a structured assessment.

TODAY'S ANALYSIS SCORES ({len(scores)} symbols):
{score_text}

Produce a JSON response with:
- "top_buys": array of {{"symbol": "TICKER", "score": N, "reason": "one sentence"}} (top 5)
- "top_avoids": array of {{"symbol": "TICKER", "score": N, "reason": "one sentence"}} (bottom 5)
- "portfolio_risk": "one paragraph about overall market conditions and risk"
- "per_symbol_notes": {{"TICKER": "note"}} for any symbol with unusual activity

Output ONLY valid JSON. No explanation outside the JSON."""

    try:
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {deepseek_key}"},
                json={
                    "model": "deepseek-reasoner",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2000,
                },
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]

        # / parse structured response
        import re
        text = raw.strip()
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
        result = json.loads(text)

        # / store to db
        await store_daily_synthesis(
            pool, today, "deepseek-reasoner",
            result.get("top_buys"),
            result.get("top_avoids"),
            result.get("portfolio_risk"),
            result.get("per_symbol_notes"),
            raw,
        )

        logger.info("daily_synthesis_complete",
            buys=len(result.get("top_buys", [])),
            avoids=len(result.get("top_avoids", [])),
        )
        return result

    except Exception as exc:
        logger.error("daily_synthesis_failed", error=str(exc))
        # / store raw response even on parse failure
        try:
            await store_daily_synthesis(
                pool, today, "deepseek-reasoner",
                None, None, None, None,
                str(exc),
            )
        except Exception:
            pass
        return None
