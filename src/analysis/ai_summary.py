# / ai-powered analysis summary using groq free tier
# / takes all analysis results for a symbol and produces natural language summary
# / graceful: returns formatted fallback if groq unavailable

from __future__ import annotations

import os
import re
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
MAX_TOKENS = 1200


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
    consensus_confidence: float = 0.0


def _build_prompt(
    symbol: str,
    ratio: RatioScore | None,
    dcf: DCFResult | None,
    earnings: EarningsSignal | None,
    insider: InsiderSignal | None,
    regime: str | None = None,
    indicators: dict | None = None,
    sentiment: dict | None = None,
    positions: list[dict] | None = None,
) -> str:
    # / construct structured analysis prompt with analytical instructions
    parts = [f"Analyze {symbol} and provide an investment signal."]
    parts.append("Begin with SIGNAL: BULLISH, SIGNAL: BEARISH, or SIGNAL: NEUTRAL.")

    if regime:
        parts.append(f"\n## Market Context\nRegime: {regime}")

    if ratio:
        parts.append(f"\n## Valuation vs Peers")
        parts.append(f"Ratio score: {ratio.composite_score}/100")
        if ratio.details.get("pe_ratio") is not None:
            pe = float(ratio.details["pe_ratio"])
            sector_pe = ratio.details.get("sector_pe_avg")
            premium = ""
            if sector_pe and sector_pe != "N/A" and float(sector_pe) > 0:
                prem = (pe / float(sector_pe) - 1) * 100
                premium = f" ({prem:+.0f}% vs sector)"
            parts.append(f"  P/E: {pe:.1f}{premium}")
        if ratio.details.get("fcf_margin") is not None:
            parts.append(f"  FCF margin: {float(ratio.details['fcf_margin']):.1%}")
        if ratio.details.get("debt_to_equity") is not None:
            parts.append(f"  Debt/Equity: {float(ratio.details['debt_to_equity']):.1f}")

    if dcf:
        parts.append(f"\n## DCF Model Output (challenge these assumptions)")
        parts.append(f"  Fair value: ${dcf.fair_value_median:.2f} | Current: ${dcf.current_price:.2f} | Upside: {dcf.upside_pct:.1%}")
        parts.append(f"  Range: ${dcf.fair_value_p10:.2f} to ${dcf.fair_value_p90:.2f} | Confidence: {dcf.confidence}")
        if abs(dcf.upside_pct) > 0.5:
            parts.append(f"  NOTE: Extreme {dcf.upside_pct:.0%} gap. Consider whether model assumptions match this company's growth profile.")

    if earnings:
        parts.append(f"\n## Earnings Momentum")
        parts.append(f"  Signal: {earnings.signal} | Strength: {earnings.strength}")
        if earnings.surprise_pct is not None:
            parts.append(f"  Latest surprise: {float(earnings.surprise_pct):.1%} | Consecutive beats: {earnings.consecutive_beats}")

    if insider:
        parts.append(f"\n## Insider Activity (last 90 days)")
        parts.append(f"  Signal: {insider.signal} (strength {insider.strength}/100)")
        parts.append(f"  {insider.total_buys} buys, {insider.total_sells} sells (net buy ratio: {insider.net_buy_ratio:.2f})")
        if insider.cluster_detected:
            parts.append(f"  Cluster buying detected. Multiple insiders buying within 30 days.")
        if insider.details:
            wb = float(insider.details.get("weighted_buy_value", 0))
            ws = float(insider.details.get("weighted_sell_value", 0))
            if wb > 0 or ws > 0:
                parts.append(f"  Buy volume: ${wb:,.0f}, Sell volume: ${ws:,.0f}")
        if insider.top_trades:
            parts.append(f"  Key trades:")
            # / map transaction types to human-readable actions
            _action_map = {
                "buy": "bought",
                "sell": "sold",
                "option_exercise": "exercised options (not a market sale)",
                "tax_payment": "withheld shares for tax (automatic)",
                "gift": "gifted shares (not a market sale)",
            }
            for t in insider.top_trades[:5]:
                title = f" ({t['title']})" if t.get("title") else ""
                action = _action_map.get(t["type"], t["type"])
                shares = int(float(t.get("shares", 0)))
                value = float(t.get("value", 0))
                parts.append(f"    {t['name']}{title} {action} {shares:,} shares (${value:,.0f}) on {t['date']}")
        # / notes for non-conviction transactions excluded from signal
        if insider.details:
            oe = insider.details.get("option_exercise_count", 0)
            tp = insider.details.get("tax_payment_count", 0)
            gc = insider.details.get("gift_count", 0)
            if oe > 0:
                parts.append(f"  Note: {oe} option exercises excluded (RSU/stock vesting, not market sales)")
            if tp > 0:
                parts.append(f"  Note: {tp} tax withholding transactions excluded (automatic, not market sales)")
            if gc > 0:
                parts.append(f"  Note: {gc} gift transactions excluded (not market sales)")

    if indicators:
        parts.append(f"\n## Technical Setup")
        rsi = indicators.get("rsi14")
        if rsi is not None:
            rsi = float(rsi)
            label = "overbought, watch for reversal" if rsi > 70 else "oversold, potential bounce" if rsi < 30 else "neutral zone"
            parts.append(f"  RSI(14): {rsi:.1f} ({label})")
        macd_h = indicators.get("macd_histogram")
        if macd_h is not None:
            macd_h = float(macd_h)
            parts.append(f"  MACD histogram: {macd_h:.4f} ({'bullish momentum' if macd_h > 0 else 'bearish momentum'})")
        adx = indicators.get("adx")
        if adx is not None:
            adx = float(adx)
            parts.append(f"  ADX: {adx:.1f} ({'strong trend' if adx > 25 else 'weak/no trend'})")

    if sentiment:
        parts.append(f"\n## Sentiment")
        ns = sentiment.get("news_score")
        if ns is not None:
            ns = float(ns)
            label = "bullish" if ns > 0.1 else "bearish" if ns < -0.1 else "neutral"
            parts.append(f"  News: {ns:.2f} ({label})")
        social = sentiment.get("social")
        if social:
            vol = social.get("volume", 0)
            bull = social.get("bullish_pct")
            if vol or bull is not None:
                s = f"  Social: {vol} mentions" if vol else "  Social:"
                if bull is not None:
                    s += f", {float(bull):.0%} bullish"
                parts.append(s)

    if positions:
        parts.append(f"\n## Current Positions")
        parts.append(f"{len(positions)} strategies currently hold this stock:")
        for p in positions:
            entry = f" @ ${p['avg_entry_price']:.2f}" if p.get("avg_entry_price") else ""
            parts.append(f"  - {p['strategy_id']}: {p['qty']:.0f} shares{entry}")

    parts.append("\n## Instructions")
    parts.append("- Identify the 2-3 most important signals and explain why they matter more than the others")
    parts.append("- If signals conflict, state which you trust and why")
    parts.append("- Keep under 200 words")

    return "\n".join(parts)


def _build_crypto_prompt(
    symbol: str,
    nvt: float | None = None,
    funding_rate: float | None = None,
    oi_rank: int | None = None,
    price_change_24h: float | None = None,
    price_change_7d: float | None = None,
    market_cap: float | None = None,
    fear_greed: float | None = None,
    sentiment_score: float | None = None,
    regime: str | None = None,
    positions: list[dict] | None = None,
) -> str:
    # / construct crypto-specific analysis prompt
    parts = [f"Analyze {symbol} and provide a trading signal."]
    parts.append("Begin with SIGNAL: BULLISH, SIGNAL: BEARISH, or SIGNAL: NEUTRAL.")

    if regime:
        parts.append(f"\n## Market Regime\n{regime}")

    if nvt is not None:
        parts.append(f"\n## On-chain Valuation")
        label = "healthy network usage" if nvt < 15 else "overvalued, speculative" if nvt > 25 else "moderate"
        parts.append(f"  NVT ratio: {nvt:.1f} ({label})")

    has_deriv = funding_rate is not None or oi_rank is not None
    if has_deriv:
        parts.append(f"\n## Derivatives & Funding")
        if funding_rate is not None:
            label = "crowded long, liquidation risk" if funding_rate > 0.0005 else "shorts paying longs" if funding_rate < -0.0001 else "balanced"
            parts.append(f"  Funding rate: {funding_rate:+.4%} ({label})")
        if oi_rank is not None:
            parts.append(f"  Open interest rank: #{oi_rank}")

    has_momentum = any(x is not None for x in [price_change_24h, price_change_7d, market_cap])
    if has_momentum:
        parts.append(f"\n## Price Momentum")
        if price_change_24h is not None:
            parts.append(f"  24h change: {price_change_24h:+.1%}")
        if price_change_7d is not None:
            parts.append(f"  7d change: {price_change_7d:+.1%}")
        if market_cap is not None:
            parts.append(f"  Market cap: ${market_cap / 1e9:.1f}B")

    has_sentiment = fear_greed is not None or sentiment_score is not None
    if has_sentiment:
        parts.append(f"\n## Sentiment")
        if fear_greed is not None:
            label = "extreme fear" if fear_greed < 25 else "fear" if fear_greed < 40 else "neutral" if fear_greed < 60 else "greed" if fear_greed < 75 else "extreme greed"
            parts.append(f"  Fear & Greed: {fear_greed:.0f}/100 ({label})")
        if sentiment_score is not None:
            parts.append(f"  News sentiment: {sentiment_score:+.2f}")

    if positions:
        parts.append(f"\n## Current Positions")
        parts.append(f"{len(positions)} strategies currently hold this asset:")
        for p in positions:
            entry = f" @ ${p['avg_entry_price']:.2f}" if p.get("avg_entry_price") else ""
            parts.append(f"  - {p['strategy_id']}: {p['qty']:.4f} units{entry}")

    parts.append(f"\n## Analysis Framework")
    parts.append("Consider these factors:")
    parts.append("1. Momentum: is the trend sustainable or extended? Look at 24h vs 7d divergence.")
    parts.append("2. Sentiment: does fear/greed suggest contrarian positioning? Extreme greed (>75) often precedes corrections.")
    parts.append("3. Funding rate risk: positive funding above 0.03% means longs paying a premium.")
    parts.append("4. On-chain health: NVT below 15 suggests real usage supports price. Above 25 suggests speculation.")
    parts.append("5. Market structure: does the regime align with momentum and sentiment?")
    parts.append("\nKeep under 200 words. Focus on actionable insight and risk assessment.")

    return "\n".join(parts)


def _build_crypto_fallback_summary(
    symbol: str,
    nvt: float | None = None,
    funding_rate: float | None = None,
    price_change_7d: float | None = None,
    sentiment_score: float | None = None,
) -> AnalysisSummary:
    # / structured fallback for crypto when llms unavailable
    signals: list[str] = []
    bullish_count = 0
    bearish_count = 0

    if nvt is not None:
        if nvt < 15:
            signals.append(f"NVT: {nvt:.1f} (healthy usage)")
            bullish_count += 1
        elif nvt > 25:
            signals.append(f"NVT: {nvt:.1f} (overvalued)")
            bearish_count += 1
        else:
            signals.append(f"NVT: {nvt:.1f} (moderate)")

    if funding_rate is not None:
        if funding_rate < -0.0001:
            signals.append(f"Funding: {funding_rate:+.4%} (shorts paying)")
            bullish_count += 1
        elif funding_rate > 0.0005:
            signals.append(f"Funding: {funding_rate:+.4%} (crowded long)")
            bearish_count += 1

    if price_change_7d is not None:
        if price_change_7d > 0.03:
            signals.append(f"7d momentum: {price_change_7d:+.1%}")
            bullish_count += 1
        elif price_change_7d < -0.03:
            signals.append(f"7d momentum: {price_change_7d:+.1%}")
            bearish_count += 1

    if sentiment_score is not None:
        if sentiment_score > 0.1:
            signals.append(f"Sentiment: {sentiment_score:+.2f} (positive)")
            bullish_count += 1
        elif sentiment_score < -0.1:
            signals.append(f"Sentiment: {sentiment_score:+.2f} (negative)")
            bearish_count += 1

    if bullish_count > bearish_count:
        overall = "bullish"
    elif bearish_count > bullish_count:
        overall = "bearish"
    else:
        overall = "neutral"

    summary = f"{symbol} — {overall.upper()}\n" + "\n".join(f"• {s}" for s in signals)
    return AnalysisSummary(
        symbol=symbol, date=date.today(), summary=summary,
        model_used=None, signal=overall, confidence=50.0 if signals else 30.0,
    )


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


_BULLISH_KEYWORDS = ("bullish", "buy", "upside", "undervalued", "outperform", "favorable")
_BEARISH_KEYWORDS = ("bearish", "sell", "downside", "overvalued", "underperform", "unfavorable", "avoid")
_NEGATION_WORDS = {"not", "no", "neither", "nor", "unlikely", "hardly", "without", "lack", "don't", "doesn't", "isn't", "aren't"}
_PREFIX_RE = re.compile(
    r"^\s*\*{0,2}\s*signal\s*:\s*\*{0,2}\s*(bullish|bearish|neutral)\s*\*{0,2}",
    re.IGNORECASE,
)
_TRANSITION_RE = re.compile(
    r"(bullish|bearish|neutral)\s+to\s+(bullish|bearish|neutral)",
    re.IGNORECASE,
)


def _extract_signal(text: str) -> tuple[str, float]:
    # / extract signal and confidence from llm response
    if not text or not text.strip():
        return ("neutral", 30.0)

    # / phase 1: structured prefix match
    match = _PREFIX_RE.search(text[:80])
    if match:
        return (match.group(1).lower(), 90.0)

    # / also check unstructured opening: "Bullish." or "**Bearish**"
    stripped = text.strip().lstrip("*").strip()
    first_word = stripped.split()[0].rstrip(".*,:;!").lower() if stripped else ""
    if first_word in ("bullish", "bearish", "neutral"):
        return (first_word, 90.0)

    # / phase 2: paragraph-weighted keyword analysis
    paragraphs = text.split("\n\n")
    if len(paragraphs) == 1:
        paragraphs = [p for p in text.split("\n") if p.strip()]
    if not paragraphs:
        return ("neutral", 30.0)

    bullish_score = 0.0
    bearish_score = 0.0

    for i, para in enumerate(paragraphs):
        weight = 2.0 if i == len(paragraphs) - 1 else 1.0
        lower_para = para.lower()

        for trans_match in _TRANSITION_RE.finditer(lower_para):
            target = trans_match.group(2).lower()
            if target == "bullish":
                bullish_score += weight
            elif target == "bearish":
                bearish_score += weight

        words = lower_para.split()
        for j, word in enumerate(words):
            clean = word.strip(".,;:!?*()[]\"'")
            is_bull = clean in _BULLISH_KEYWORDS
            is_bear = clean in _BEARISH_KEYWORDS
            if not is_bull and not is_bear:
                continue
            preceding = {w.strip(".,;:!?*()[]\"'") for w in words[max(0, j - 4):j]}
            negated = bool(preceding & _NEGATION_WORDS)
            if j + 2 < len(words) and words[j + 1] == "to":
                next_clean = words[j + 2].strip(".,;:!?*()[]\"'")
                if next_clean in ("bullish", "bearish", "neutral"):
                    continue
            if is_bull:
                if negated:
                    bearish_score += weight
                else:
                    bullish_score += weight
            elif is_bear:
                if negated:
                    bullish_score += weight
                else:
                    bearish_score += weight

    if bullish_score == 0.0 and bearish_score == 0.0:
        return ("neutral", 30.0)

    total = bullish_score + bearish_score
    if bullish_score > bearish_score:
        signal, ratio = "bullish", bullish_score / total
    elif bearish_score > bullish_score:
        signal, ratio = "bearish", bearish_score / total
    else:
        return ("neutral", 50.0)

    return (signal, 70.0 if ratio >= 0.667 else 50.0)


_EQUITY_SYSTEM_MSG = (
    "You are a quantitative equity analyst. Analyze the data provided and produce a structured signal.\n"
    "Rules:\n"
    "- Begin with SIGNAL: BULLISH, SIGNAL: BEARISH, or SIGNAL: NEUTRAL\n"
    "- When data conflicts, state which signal you trust more and why\n"
    "- Weight technicals and fundamentals equally\n"
    "- If DCF shows extreme upside/downside (>50%), question whether assumptions are reasonable\n"
    "- Note market regime but don't let it override strong company-specific signals"
)

_DEEPSEEK_SYSTEM_MSG = (
    "You are an independent senior equity analyst providing a second opinion.\n"
    "Rules:\n"
    "- Begin with SIGNAL: BULLISH, SIGNAL: BEARISH, or SIGNAL: NEUTRAL\n"
    "- Focus on what others might miss: business quality, competitive moat, growth trajectory\n"
    "- Challenge the DCF valuation. Is the terminal multiple appropriate?\n"
    "- If insider selling is heavy, distinguish routine (RSU vesting, tax) vs conviction selling\n"
    "- Provide concrete risk/reward assessment"
)

_CRYPTO_SYSTEM_MSG = (
    "You are a crypto market analyst. Analyze on-chain, derivatives, and sentiment data.\n"
    "Rules:\n"
    "- Begin with SIGNAL: BULLISH, SIGNAL: BEARISH, or SIGNAL: NEUTRAL\n"
    "- Weight funding rate risk and sentiment extremes heavily\n"
    "- Consider contrarian positioning when fear/greed is extreme\n"
    "- Provide concrete risk assessment"
)


class _RateLimited(Exception):
    pass


async def _call_llm(
    api_key: str, model: str, prompt: str, symbol: str,
    system_message: str = _EQUITY_SYSTEM_MSG,
    max_retries: int = 2,
) -> AnalysisSummary | None:
    # / single llm api call with retry on 429, raises _RateLimited after exhausting retries
    import asyncio
    import httpx
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_message},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": MAX_TOKENS,
                        "temperature": 0.3,
                    },
                )
                if resp.status_code == 429:
                    # / backoff: 4s, 8s between retries
                    if attempt < max_retries:
                        wait = (attempt + 1) * 4
                        logger.info("llm_rate_limited_retrying", symbol=symbol, model=model, wait=wait, attempt=attempt + 1)
                        await asyncio.sleep(wait)
                        continue
                    logger.info("llm_rate_limited_exhausted", symbol=symbol, model=model)
                    raise _RateLimited()
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]
                summary_text = choice["message"]["content"].strip()
                # / detect truncated output from max_tokens hit
                if choice.get("finish_reason") == "length":
                    logger.info("llm_output_truncated", symbol=symbol, model=model, tokens=MAX_TOKENS)
                signal, confidence = _extract_signal(summary_text)
                logger.info("ai_summary_generated", symbol=symbol, model=model)
                return AnalysisSummary(
                    symbol=symbol, date=date.today(), summary=summary_text,
                    model_used=model, signal=signal, confidence=confidence,
                )
        except _RateLimited:
            raise
        except Exception as exc:
            logger.info("llm_model_failed", symbol=symbol, model=model, error=str(exc)[:100])
            return None
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
    crypto_data: dict | None = None,
    positions: list[dict] | None = None,
) -> AnalysisSummary:
    # / try groq llm, fall back to structured summary
    if crypto_data is not None:
        prompt = _build_crypto_prompt(**crypto_data, positions=positions)
        sys_msg = _CRYPTO_SYSTEM_MSG
    else:
        prompt = _build_prompt(symbol, ratio, dcf, earnings, insider, regime,
                               indicators=indicators, sentiment=sentiment,
                               positions=positions)
        sys_msg = _EQUITY_SYSTEM_MSG

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.info("groq_api_key_missing_using_fallback", symbol=symbol)
        if crypto_data is not None:
            return _build_crypto_fallback_summary(
                symbol, nvt=crypto_data.get("nvt"), funding_rate=crypto_data.get("funding_rate"),
                price_change_7d=crypto_data.get("price_change_7d"), sentiment_score=crypto_data.get("sentiment_score"),
            )
        return _build_fallback_summary(symbol, ratio, dcf, earnings, insider)

    # / try models in order: 120b → default → fallback 20b
    # / _call_llm retries 429 internally; if still limited, try next model
    models = ["openai/gpt-oss-120b", DEFAULT_MODEL, FALLBACK_MODEL]
    for model in models:
        try:
            result = await _call_llm(api_key, model, prompt, symbol, system_message=sys_msg)
            if result:
                return result
        except _RateLimited:
            continue

    logger.warning("all_llm_models_failed_using_fallback", symbol=symbol)
    if crypto_data is not None:
        return _build_crypto_fallback_summary(
            symbol, nvt=crypto_data.get("nvt"), funding_rate=crypto_data.get("funding_rate"),
            price_change_7d=crypto_data.get("price_change_7d"), sentiment_score=crypto_data.get("sentiment_score"),
        )
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
    crypto_data: dict | None = None,
    positions: list[dict] | None = None,
) -> AnalysisSummary | None:
    # / independent second opinion via deepseek — gets same raw data, NOT groq's output
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    if crypto_data is not None:
        prompt = _build_crypto_prompt(**crypto_data, positions=positions)
        sys_msg = _CRYPTO_SYSTEM_MSG
    else:
        prompt = _build_prompt(symbol, ratio, dcf, earnings, insider, regime,
                               indicators=indicators, sentiment=sentiment,
                               positions=positions)
        sys_msg = _DEEPSEEK_SYSTEM_MSG

    # / retry once on 429 or transient errors
    import asyncio
    import httpx
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(
                    f"{DEEPSEEK_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": DEEPSEEK_MODEL,
                        "messages": [
                            {"role": "system", "content": sys_msg},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": MAX_TOKENS,
                        "temperature": 0.3,
                    },
                )
                if resp.status_code == 429 and attempt == 0:
                    logger.info("deepseek_rate_limited_retrying", symbol=symbol)
                    await asyncio.sleep(5)
                    continue
                resp.raise_for_status()
                data = resp.json()
                summary_text = data["choices"][0]["message"]["content"].strip()
                signal, confidence = _extract_signal(summary_text)
                logger.info("deepseek_summary_generated", symbol=symbol)
                return AnalysisSummary(
                    symbol=symbol, date=date.today(), summary=summary_text,
                    model_used=DEEPSEEK_MODEL, signal=signal, confidence=confidence,
                )
        except Exception as exc:
            if attempt == 0:
                logger.info("deepseek_attempt_failed_retrying", symbol=symbol, error=str(exc)[:100])
                await asyncio.sleep(3)
                continue
            logger.warning("deepseek_api_failed", symbol=symbol, error=str(exc))
            return None
    return None


def _compute_consensus(
    groq: AnalysisSummary,
    deepseek: AnalysisSummary | None,
) -> tuple[str, float]:
    # / compute consensus signal and confidence from two independent analyses
    if deepseek is None:
        return (groq.signal, groq.confidence)
    if groq.signal == deepseek.signal:
        return (groq.signal, max(groq.confidence, deepseek.confidence))
    # / soft disagree: one neutral, other directional
    if groq.signal == "neutral" and deepseek.signal in ("bullish", "bearish"):
        return (deepseek.signal, round(max(0.0, min(100.0, deepseek.confidence - 15.0)), 1))
    if deepseek.signal == "neutral" and groq.signal in ("bullish", "bearish"):
        return (groq.signal, round(max(0.0, min(100.0, groq.confidence - 15.0)), 1))
    # / hard disagree
    gap = abs(groq.confidence - deepseek.confidence)
    if gap > 15.0:
        winner = groq if groq.confidence > deepseek.confidence else deepseek
        return (winner.signal, round(max(0.0, min(100.0, winner.confidence - 10.0)), 1))
    return ("disagree", round((groq.confidence + deepseek.confidence) / 2.0, 1))


async def generate_dual_analysis(
    symbol: str,
    ratio: RatioScore | None = None,
    dcf: DCFResult | None = None,
    earnings: EarningsSignal | None = None,
    insider: InsiderSignal | None = None,
    regime: str | None = None,
    indicators: dict | None = None,
    sentiment: dict | None = None,
    crypto_data: dict | None = None,
    positions: list[dict] | None = None,
) -> DualAnalysis:
    # / run groq + deepseek in parallel, compute consensus
    import asyncio
    groq_task = generate_summary(symbol, ratio, dcf, earnings, insider, regime,
                                 indicators=indicators, sentiment=sentiment,
                                 crypto_data=crypto_data, positions=positions)
    deepseek_task = _generate_deepseek_summary(symbol, ratio, dcf, earnings, insider, regime,
                                                indicators=indicators, sentiment=sentiment,
                                                crypto_data=crypto_data, positions=positions)

    groq_result, deepseek_result = await asyncio.gather(groq_task, deepseek_task)

    consensus, consensus_confidence = _compute_consensus(groq_result, deepseek_result)

    logger.info(
        "dual_analysis_complete", symbol=symbol,
        groq=groq_result.signal, deepseek=deepseek_result.signal if deepseek_result else "unavailable",
        consensus=consensus, consensus_confidence=consensus_confidence,
    )

    return DualAnalysis(
        groq=groq_result, deepseek=deepseek_result,
        consensus=consensus, consensus_confidence=consensus_confidence,
    )


async def generate_daily_synthesis(
    pool: Any,
    symbols: list[str],
) -> dict[str, Any] | None:
    # / 5PM ET portfolio-wide synthesis via deepseek-reasoner
    # / reads all today's data, produces top buys/avoids/risk assessment
    import json
    from src.agents.tools import store_daily_synthesis

    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if not deepseek_key:
        logger.info("no_deepseek_key_skipping_synthesis")
        return None

    # / gather latest analysis scores — use today, fall back to most recent day
    from datetime import timedelta as _td
    today = date.today()
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
            # / if no scores today (weekend/holiday/timing), grab most recent day
            if not scores:
                rows = await conn.fetch(
                    """SELECT symbol, composite_score, regime,
                        details->>'ai_consensus' as ai_consensus
                    FROM analysis_scores
                    WHERE date >= $1
                    ORDER BY composite_score DESC""",
                    today - _td(days=3),
                )
                scores = [dict(r) for r in rows]
                if scores:
                    logger.info("synthesis_using_recent_scores", count=len(scores), lookback_days=3)
    except Exception as exc:
        logger.warning("synthesis_fetch_scores_failed", error=str(exc))

    if not scores:
        logger.info("synthesis_no_data_recent")
        return None

    # / build the prompt
    top_scores = scores[:30]
    bottom_scores = scores[-20:] if len(scores) > 30 else []
    score_lines = []
    for s in top_scores:
        score_lines.append(
            f"  {s.get('symbol', '?')}: score={s.get('composite_score', 0)}, "
            f"consensus={s.get('ai_consensus', '--')}, regime={s.get('regime', '--')}"
        )
    if bottom_scores:
        score_lines.append("\n  --- BOTTOM PERFORMERS ---")
        for s in bottom_scores:
            score_lines.append(
                f"  {s.get('symbol', '?')}: score={s.get('composite_score', 0)}, "
                f"consensus={s.get('ai_consensus', '--')}, regime={s.get('regime', '--')}"
            )
    score_text = "\n".join(score_lines)

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
                    "messages": [
                        {"role": "system", "content": "You are a senior portfolio analyst. Produce structured JSON assessments."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 2000,
                },
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]

        # / parse structured response
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
