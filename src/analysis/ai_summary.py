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

# / groq free tier: llama models, 30 req/min
DEFAULT_MODEL = "llama-3.1-8b-instant"
MAX_TOKENS = 500


@dataclass
class AnalysisSummary:
    symbol: str
    date: date
    summary: str
    model_used: str | None  # none if fallback was used
    signal: str             # bullish, bearish, neutral
    confidence: float       # 0-100


def _build_prompt(
    symbol: str,
    ratio: RatioScore | None,
    dcf: DCFResult | None,
    earnings: EarningsSignal | None,
    insider: InsiderSignal | None,
    regime: str | None = None,
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


async def generate_summary(
    symbol: str,
    ratio: RatioScore | None = None,
    dcf: DCFResult | None = None,
    earnings: EarningsSignal | None = None,
    insider: InsiderSignal | None = None,
    regime: str | None = None,
) -> AnalysisSummary:
    # / try groq llm, fall back to structured summary
    prompt = _build_prompt(symbol, ratio, dcf, earnings, insider, regime)

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.info("groq_api_key_missing_using_fallback", symbol=symbol)
        return _build_fallback_summary(symbol, ratio, dcf, earnings, insider)

    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEFAULT_MODEL,
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
            model_used = data.get("model", DEFAULT_MODEL)

            # / infer signal from summary
            lower = summary_text.lower()
            if "bullish" in lower[:50]:
                signal = "bullish"
            elif "bearish" in lower[:50]:
                signal = "bearish"
            else:
                signal = "neutral"

            logger.info("ai_summary_generated", symbol=symbol, model=model_used)

            return AnalysisSummary(
                symbol=symbol,
                date=date.today(),
                summary=summary_text,
                model_used=model_used,
                signal=signal,
                confidence=75.0,  # llm summaries get moderate confidence
            )

    except Exception as exc:
        logger.warning("groq_api_failed_using_fallback", symbol=symbol, error=type(exc).__name__)
        return _build_fallback_summary(symbol, ratio, dcf, earnings, insider)
