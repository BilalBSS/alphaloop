# / analyst agent — runs fundamental analysis pipeline per symbol
# / writes composite scores to analysis_scores table
# / graceful: one symbol failure doesn't stop the batch

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog

import os

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
from src.quant import kronos_signal

logger = structlog.get_logger(__name__)


async def _noop():
    # / placeholder for parallel gather slots that should be skipped (e.g. insider for etfs)
    return None


async def _compute_kronos_score(pool, symbol: str) -> tuple[float | None, dict | None]:
    # / returns (score_0_100, details_dict) — returns (None, None) when disabled or short data
    # / weight 0.15 in the composite when present (see _apply_kronos_to_composite below)
    try:
        import pandas as pd
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT open, high, low, close, volume
                FROM market_data
                WHERE symbol = $1
                ORDER BY date DESC
                LIMIT 120""",
                symbol,
            )
        if not rows or len(rows) < 32:
            return None, None
        df = pd.DataFrame([{
            "open":   float(r["open"]) if r["open"] is not None else 0.0,
            "high":   float(r["high"]) if r["high"] is not None else 0.0,
            "low":    float(r["low"]) if r["low"] is not None else 0.0,
            "close":  float(r["close"]) if r["close"] is not None else 0.0,
            "volume": float(r["volume"]) if r["volume"] is not None else 0.0,
        } for r in reversed(rows)])  # / oldest-first for kronos
        pred = kronos_signal.predict(symbol, df, lookback=64)
        if pred.source == "insufficient_data":
            return None, None
        score = round(pred.probability * 100.0, 1)  # / map [0,1] -> [0,100]
        return score, {
            "probability": round(pred.probability, 4),
            "confidence":  round(pred.confidence, 4),
            "source":      pred.source,
            "components":  pred.components,
        }
    except Exception as exc:
        logger.debug("kronos_score_failed", symbol=symbol, error=str(exc)[:120])
        return None, None


def _kronos_weight() -> float:
    # / analyst blend weight for Kronos; 0 disables without requiring env tweak
    raw = os.environ.get("KRONOS_COMPOSITE_WEIGHT", "0.15")
    try:
        w = float(raw)
    except ValueError:
        w = 0.15
    return max(0.0, min(0.5, w))


def _compute_technical_score(indicator_data: dict | None) -> float | None:
    # / bug 4c: derive 0-100 technical score from indicator_data so analysis_scores.technical_score
    # / stops being null. keys match what _fetch_equity_enrichment stores: rsi14, macd_histogram, adx.
    # / rsi and macd carry direction; adx is trend strength only and acts as confidence multiplier.
    if not indicator_data:
        return None

    direction_parts: list[float] = []

    # / rsi14: 50 = neutral, <30 oversold (bullish), >70 overbought (bearish)
    rsi = indicator_data.get("rsi14") or indicator_data.get("rsi")
    if rsi is not None:
        try:
            r = float(rsi)
            direction_parts.append(max(0.0, min(100.0, 100.0 - r)))
        except (TypeError, ValueError):
            pass

    # / macd histogram: positive = bullish, negative = bearish, squashed around 50 midpoint
    macd_hist = indicator_data.get("macd_histogram") or indicator_data.get("macd_hist")
    if macd_hist is not None:
        try:
            h = float(macd_hist)
            direction_parts.append(50.0 + 25.0 * (1.0 if h > 0 else -1.0) * min(1.0, abs(h) / 2.0))
        except (TypeError, ValueError):
            pass

    if not direction_parts:
        return None

    base = sum(direction_parts) / len(direction_parts)

    # / adx as confidence: strong trends (adx > 25) pull the score away from 50 toward the direction
    # / weak trends (adx < 25) pull the score TOWARD 50. max amplification 1.3x, max damping 0.7x.
    adx = indicator_data.get("adx")
    if adx is not None:
        try:
            a = float(adx)
            # / linear: adx=10 → 0.7x, adx=25 → 1.0x, adx=50 → ~1.3x
            mult = 0.7 + min(0.6, max(0.0, a / 50.0) * 0.6)
            score = 50.0 + (base - 50.0) * mult
            base = max(0.0, min(100.0, score))
        except (TypeError, ValueError):
            pass

    return round(base, 1)


async def order_symbols_by_staleness(
    pool, symbols: list[str], min_refresh_interval_s: float = 0.0,
) -> list[str]:
    # / order symbols oldest-first by latest analysis_scores.created_at so analyst
    # / always refreshes the most-stale first. never-scored symbols jump to front.
    # / symbols scored within min_refresh_interval_s are dropped (anti-thrash).
    # / pure read, no writes. on db error returns input order unchanged.
    if not symbols:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT symbol, MAX(created_at) AS last_scored
                FROM analysis_scores
                WHERE symbol = ANY($1::text[])
                GROUP BY symbol""",
                symbols,
            )
    except Exception as exc:
        logger.debug("analyst_staleness_query_failed", error=str(exc)[:120])
        return list(symbols)
    last_scored: dict[str, datetime] = {}
    for r in rows:
        ts = r["last_scored"]
        if ts is not None:
            # / asyncpg returns tz-naive for TIMESTAMP, tz-aware for TIMESTAMPTZ.
            # / analysis_scores.created_at is TIMESTAMPTZ → always aware, but be defensive.
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            last_scored[r["symbol"]] = ts

    now = datetime.now(tz=timezone.utc)
    min_interval = timedelta(seconds=max(0.0, min_refresh_interval_s))

    # / never-scored symbols first (in input order so callers can bias universe priority)
    never_scored = [s for s in symbols if s not in last_scored]
    # / scored symbols oldest-first, skipping too-recent ones when min_interval > 0
    scored = [(s, last_scored[s]) for s in symbols if s in last_scored]
    scored.sort(key=lambda x: x[1])
    eligible_scored = [
        s for s, ts in scored
        if min_interval.total_seconds() == 0 or (now - ts) >= min_interval
    ]
    return never_scored + eligible_scored


async def get_coverage_pct(pool, symbols: list[str], window_s: float = 3600.0) -> float:
    # / fraction of `symbols` with an analysis_scores row in the last window_s seconds.
    # / returned as 0..1. used by /api/phase5-metrics to surface analyst freshness.
    # / 0.0 on error so the metric visibly flags the problem instead of pretending ok.
    if not symbols:
        return 0.0
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT COUNT(DISTINCT symbol) AS fresh
                FROM analysis_scores
                WHERE symbol = ANY($1::text[])
                AND created_at >= NOW() - ($2::int * INTERVAL '1 second')""",
                symbols, int(window_s),
            )
        fresh = int(row["fresh"] or 0)
        return round(fresh / len(symbols), 4)
    except Exception as exc:
        logger.debug("analyst_coverage_query_failed", error=str(exc)[:120])
        return 0.0


class AnalystAgent:
    # / stateless — all persistent state lives in the database

    def __init__(self):
        self._funding_cache: dict | None = None
        self._macro_cache: dict | None = None

    async def run(
        self,
        pool,
        symbols: list[str],
        run_deepseek: bool = True,
        wall_clock_budget_s: float | None = None,
        per_symbol_timeout_s: float = 60.0,
        min_refresh_interval_s: float = 0.0,
        inter_symbol_sleep_s: float = 0.5,
    ) -> dict[str, float | None]:
        # / batched analysis cycle. processes oldest-scored symbols first until
        # / wall_clock_budget_s elapses. the orchestrator sets a short budget
        # / (e.g. 420s) and a 10-min asyncio timeout above, so one stuck symbol
        # / can't deadlock the loop and one slow cycle can't eat the next.
        # /
        # / args:
        # /   wall_clock_budget_s: abort after N elapsed seconds. None = process all.
        # /   per_symbol_timeout_s: bound per _analyze_symbol call (prevents one bad
        # /       symbol eating the full budget).
        # /   min_refresh_interval_s: skip symbols refreshed within this window
        # /       (0 = no skip; avoids thrash when same subset keeps bubbling up).
        # /   inter_symbol_sleep_s: rate-limit gap (0.5s default; was 2s, but
        # /       per-symbol parallelization within the pipeline already serializes
        # /       LLM calls, so a long inter-symbol pause was double-counting).
        # /
        # / run_deepseek=False → groq only (20-min cadence).
        # / run_deepseek=True  → dual-llm consensus (30-min cadence).
        self._run_deepseek = run_deepseek
        self._funding_cache = None  # / reset per cycle
        self._macro_cache = None    # / reset per cycle
        results: dict[str, float | None] = {}
        timeouts = 0
        errors = 0
        budget_hit = False
        cycle_start = time.monotonic()

        # / pick the subset to process: oldest first, drop too-recent
        ordered = await order_symbols_by_staleness(
            pool, symbols, min_refresh_interval_s=min_refresh_interval_s,
        )
        skipped_recent = len(symbols) - len(ordered)
        if not ordered:
            logger.info(
                "analyst_run_no_stale_symbols",
                total=len(symbols), skipped_recent=skipped_recent,
                min_refresh_s=min_refresh_interval_s,
            )
            return results

        # / social sentiment: only for the batch we're going to process this cycle,
        # / keeps api call volume flat when cadence shortens.
        try:
            await run_social_sentiment(pool, ordered)
        except Exception as exc:
            logger.warning("social_sentiment_batch_failed", error=str(exc))

        # / budget anchored after per-cycle prelude (social sentiment, staleness
        # / query) so a slow prelude doesn't starve the per-symbol work.
        work_start = time.monotonic()
        deadline = (
            work_start + wall_clock_budget_s if wall_clock_budget_s else None
        )

        for i, symbol in enumerate(ordered):
            if deadline is not None and time.monotonic() >= deadline:
                budget_hit = True
                logger.info(
                    "analyst_budget_reached",
                    processed=len(results), remaining=len(ordered) - i,
                    budget_s=wall_clock_budget_s,
                )
                break
            try:
                score = await asyncio.wait_for(
                    self._analyze_symbol(pool, symbol),
                    timeout=per_symbol_timeout_s,
                )
                results[symbol] = score
            except asyncio.TimeoutError:
                timeouts += 1
                logger.warning(
                    "analyst_symbol_timeout", symbol=symbol,
                    timeout_s=per_symbol_timeout_s,
                )
                await tools.log_event(
                    pool, "warning", "analyst",
                    f"timeout after {per_symbol_timeout_s}s", symbol=symbol,
                )
                results[symbol] = None
            except Exception as exc:
                errors += 1
                logger.warning(
                    "analyst_symbol_failed", symbol=symbol, error=str(exc),
                )
                await tools.log_event(
                    pool, "warning", "analyst",
                    f"analysis failed: {str(exc)[:200]}", symbol=symbol,
                )
                results[symbol] = None
            # / throttle between symbols to avoid groq 429 rate limits
            if i < len(ordered) - 1:
                await asyncio.sleep(inter_symbol_sleep_s)

        successful = sum(1 for v in results.values() if v is not None)
        duration = round(time.monotonic() - cycle_start, 1)
        logger.info(
            "analyst_run_complete",
            symbols_analyzed=len(results), successful=successful,
            timeouts=timeouts, errors=errors,
            budget_hit=budget_hit, skipped_recent=skipped_recent,
            duration_s=duration,
        )
        cycle_level = "warning" if successful == 0 and len(results) > 0 else "info"
        await tools.log_event(
            pool, cycle_level, "analyst",
            f"cycle complete: {successful}/{len(results)} symbols in {duration}s"
            + (f" (budget hit, {len(ordered) - len(results)} deferred)" if budget_hit else ""),
            details={
                "successful": successful,
                "total": len(results),
                "timeouts": timeouts,
                "errors": errors,
                "skipped_recent": skipped_recent,
                "budget_hit": budget_hit,
                "duration_s": duration,
            },
        )
        return results

    async def _analyze_symbol(self, pool, symbol: str) -> float | None:
        # / route to crypto or equity analysis path
        if is_crypto(symbol):
            return await self._analyze_crypto_symbol(pool, symbol)
        return await self._analyze_equity_symbol(pool, symbol)

    async def _fetch_crypto_components(self, pool, symbol: str) -> tuple:
        # / returns (sentiment_score, nvt, coin_data, funding_rate, oi_rank)
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

        return (sentiment_score, nvt, coin_data, funding_rate, oi_rank)

    async def _analyze_crypto_symbol(self, pool, symbol: str) -> float | None:
        # / crypto: NVT from coingecko + sentiment + LLM analysis
        sentiment_score, nvt, coin_data, funding_rate, oi_rank = await self._fetch_crypto_components(pool, symbol)

        regime = await tools.fetch_latest_regime(pool, "crypto")
        fear_greed: float | None = None
        try:
            async with pool.acquire() as conn:
                fng_row = await conn.fetchrow(
                    """SELECT raw_score FROM social_sentiment
                    WHERE symbol = $1 AND source = 'fear_greed'
                    ORDER BY date DESC LIMIT 1""",
                    symbol,
                )
                if fng_row and fng_row["raw_score"] is not None:
                    fear_greed = float(fng_row["raw_score"])
        except Exception as exc:
            logger.warning(
                "analyst_enrichment_failed", symbol=symbol,
                source="fear_greed", error=str(exc)[:120],
            )

        # / llm analysis: same dual-llm path as equities
        # / 30-min cycle: groq only, hourly cycle: groq + deepseek
        ai_signal: str | None = None
        ai_summary_text: str | None = None
        crypto_data = {
            "symbol": symbol,
            "nvt": nvt,
            "funding_rate": funding_rate,
            "oi_rank": oi_rank,
            "price_change_24h": coin_data.get("price_change_24h_pct") if coin_data else None,
            "price_change_7d": coin_data.get("price_change_7d_pct") if coin_data else None,
            "market_cap": coin_data.get("market_cap") if coin_data else None,
            "fear_greed": fear_greed,
            "sentiment_score": sentiment_score,
            "regime": regime,
        }

        # / fetch strategy positions for llm context
        strat_positions = await tools.get_strategy_positions(pool, symbol=symbol)

        deepseek_text: str | None = None
        ai_confidence: float = 0.0
        if getattr(self, "_run_deepseek", True):
            # / hourly: dual-llm (groq + deepseek), same as equities
            try:
                dual = await generate_dual_analysis(
                    symbol, crypto_data=crypto_data,
                    positions=strat_positions,
                )
                ai_signal = dual.consensus
                ai_confidence = dual.consensus_confidence
                ai_summary_text = dual.groq.summary if dual.groq else None
                deepseek_text = dual.deepseek.summary if dual.deepseek else None
            except Exception as exc:
                logger.warning("analyst_crypto_dual_failed", symbol=symbol, error=str(exc))
        else:
            # / 30-min: groq only
            try:
                summary = await generate_summary(
                    symbol, crypto_data=crypto_data,
                    positions=strat_positions,
                )
                if summary:
                    ai_signal = summary.signal
                    ai_summary_text = summary.summary
                    ai_confidence = summary.confidence
            except Exception as exc:
                logger.warning("analyst_crypto_llm_failed", symbol=symbol, error=str(exc))

        # / crypto composite weights: sentiment .17, nvt .17, funding .16, momentum .17, ai .33
        components: list[tuple[float, float]] = []
        if sentiment_score is not None and sentiment_score != 0.0:
            sent_100 = max(0.0, min(100.0, (sentiment_score + 1.0) * 50.0))
            components.append((sent_100, 0.17))
        if nvt is not None:
            mvr_score = max(0.0, min(100.0, (15.0 - nvt) / 15.0 * 80.0 + 10.0))
            components.append((mvr_score, 0.17))
        if funding_rate is not None:
            fr_score = max(0.0, min(100.0, (0.01 - funding_rate) / 0.02 * 100.0))
            components.append((fr_score, 0.16))
        # / price momentum: 24h + 7d blend mapped to 0-100
        if coin_data:
            pct_24h = coin_data.get("price_change_24h_pct")
            pct_7d = coin_data.get("price_change_7d_pct")
            if pct_24h is not None or pct_7d is not None:
                avg_pct = ((pct_7d or 0) * 0.6 + (pct_24h or 0) * 0.4)
                momentum_score = max(0.0, min(100.0, (avg_pct + 30.0) / 60.0 * 100.0))
                components.append((momentum_score, 0.17))
        if ai_signal:
            signal_map = {"bullish": 80.0, "neutral": 50.0, "bearish": 20.0}
            components.append((signal_map.get(ai_signal, 50.0), 0.33))

        composite: float | None = None
        if components:
            total_w = sum(w for _, w in components)
            composite = round(sum(s * w for s, w in components) / total_w, 1)

        details: dict = {
            "nvt_ratio": nvt,
            "funding_rate": funding_rate,
            "oi_rank": oi_rank,
            "ai_consensus": ai_signal,
            "ai_consensus_confidence": ai_confidence,
            "news_sentiment_score": sentiment_score,
            "regime": regime,
        }
        # / fear_greed_index on 0-100 scale for dashboard display
        if fear_greed is not None:
            details["fear_greed_index"] = round(fear_greed * 50.0 + 50.0, 1)
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

    async def _fetch_equity_components(self, pool, symbol: str) -> tuple:
        # / returns (ratio_score, dcf_result, earnings_signal, insider_signal)
        # / fetched in parallel — all four are independent (no shared inputs).
        from src.data.symbols import get_sector
        is_etf = get_sector(symbol) == "etfs"

        async def _safe(coro, label):
            try:
                return await coro
            except Exception as exc:
                logger.warning(f"analyst_{label}_failed", symbol=symbol, error=str(exc))
                return None

        # / skip insider analysis for etfs — no form 4 filings
        coros = [
            _safe(analyze_ratios(pool, symbol), "ratio"),
            _safe(analyze_dcf(pool, symbol), "dcf"),
            _safe(analyze_earnings(symbol), "earnings"),
            _safe(analyze_insider_activity(pool, symbol), "insider") if not is_etf else _noop(),
        ]
        ratio_score, dcf_result, earnings_signal, insider_signal = await asyncio.gather(*coros)
        return (ratio_score, dcf_result, earnings_signal, insider_signal)

    async def _fetch_equity_enrichment(self, pool, symbol: str) -> tuple:
        # / returns (regime, symbol_trend, indicator_data, sentiment_data)
        regime = await tools.fetch_latest_regime(pool, "equity")

        # / compute per-symbol trend for consensus gate
        symbol_trend = "unknown"
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT close FROM market_data
                    WHERE symbol = $1 ORDER BY date DESC LIMIT 50""",
                    symbol,
                )
                if rows and len(rows) >= 50:
                    import pandas as pd
                    close_series = pd.Series([float(r["close"]) for r in reversed(rows)])
                    sma50 = close_series.rolling(window=50, min_periods=50).mean().iloc[-1]
                    if pd.notna(sma50):
                        symbol_trend = "up" if float(close_series.iloc[-1]) > float(sma50) else "down"
        except Exception as exc:
            logger.warning(
                "analyst_enrichment_failed", symbol=symbol,
                source="symbol_trend_sma50", error=str(exc)[:120],
            )

        # / fetch indicators + sentiment from db for llm prompt enrichment
        # / bug e: lazy compute from market_data if computed_indicators has no row — prevents
        # / technical_score starvation when strategy_agent hasn't written for this symbol yet
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
        except Exception as exc:
            logger.warning(
                "analyst_enrichment_failed", symbol=symbol,
                source="computed_indicators", error=str(exc)[:120],
            )
        if indicator_data is None:
            try:
                import pandas as pd
                from src.indicators.momentum import rsi
                from src.indicators.trend import macd, adx
                async with pool.acquire() as conn:
                    ohlc_rows = await conn.fetch(
                        """SELECT high, low, close FROM market_data
                        WHERE symbol = $1 ORDER BY date DESC LIMIT 100""",
                        symbol,
                    )
                if ohlc_rows and len(ohlc_rows) >= 28:
                    closes = pd.Series([float(r["close"]) for r in reversed(ohlc_rows)])
                    highs = pd.Series([float(r["high"]) for r in reversed(ohlc_rows)])
                    lows = pd.Series([float(r["low"]) for r in reversed(ohlc_rows)])
                    rsi_val = rsi(closes, 14)
                    macd_res = macd(closes, 12, 26, 9)
                    adx_val = adx(highs, lows, closes, 14)
                    indicator_data = {
                        "rsi14": float(rsi_val.iloc[-1]) if not rsi_val.empty and pd.notna(rsi_val.iloc[-1]) else None,
                        "macd_histogram": float(macd_res.histogram.iloc[-1]) if not macd_res.histogram.empty and pd.notna(macd_res.histogram.iloc[-1]) else None,
                        "adx": float(adx_val.iloc[-1]) if not adx_val.empty and pd.notna(adx_val.iloc[-1]) else None,
                    }
            except Exception as exc:
                logger.debug("analyst_lazy_indicators_failed", symbol=symbol, error=str(exc)[:100])
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
        except Exception as exc:
            logger.warning(
                "analyst_enrichment_failed", symbol=symbol,
                source="news_social_sentiment", error=str(exc)[:120],
            )

        return (regime, symbol_trend, indicator_data, sentiment_data)

    async def _fetch_alternative_data(self, pool, symbol: str) -> dict:
        from src.data.symbols import get_sector
        from src.data.fred_macro import fetch_macro_indicators, get_macro_score
        from src.data.congressional_trades import fetch_congressional_trades, compute_net_buy_ratio
        from src.data.analyst_ratings import fetch_analyst_ratings, compute_target_upside
        from src.data.earnings_revisions import fetch_earnings_estimates, compute_revision_momentum
        from src.data.short_interest import fetch_short_interest
        from src.data.dark_pool import fetch_dark_pool_data
        from src.data.options_data import fetch_options_data
        from src.data.corporate_events import days_to_earnings
        from src.indicators.intermarket import compute_intermarket

        alt: dict = {}
        is_etf = get_sector(symbol) == "etfs"

        # / macro_score uses self._macro_cache (per-cycle). hold sequentially so a
        # / single shared cache doesn't get clobbered by concurrent fetches.
        try:
            if self._macro_cache is None:
                self._macro_cache = await fetch_macro_indicators(pool)
            alt["macro_score"] = get_macro_score(self._macro_cache)
        except Exception as exc:
            self._macro_cache = {}
            logger.warning("alt_macro_failed", error=str(exc))

        # / build independent-fetch coro list. each wrapped in _safe_alt to
        # / guarantee gather doesn't propagate one failure into the others.
        async def _safe_alt(coro, label):
            try:
                return await coro
            except Exception as exc:
                logger.warning(f"alt_{label}_failed", symbol=symbol, error=str(exc))
                return None

        async def _ratings_with_target():
            # / analyst_ratings → if target_mean present, also fetch latest close
            # / for target_upside. price fetch is sequential AFTER ratings come back.
            ratings = await fetch_analyst_ratings(symbol)
            if not ratings:
                return None, None
            consensus = ratings.get("consensus_score", 0.0)
            target_mean = ratings.get("target_mean")
            upside = None
            if target_mean is not None:
                try:
                    async with pool.acquire() as conn:
                        row = await conn.fetchrow(
                            """SELECT close FROM market_data
                            WHERE symbol = $1 ORDER BY date DESC LIMIT 1""",
                            symbol,
                        )
                        if row:
                            upside = compute_target_upside(target_mean, float(row["close"]))
                except Exception as exc:
                    logger.warning(
                        "analyst_enrichment_failed", symbol=symbol,
                        source="price_target_upside", error=str(exc)[:120],
                    )
            return consensus, upside

        async def _intermarket():
            # / fetch SPY + 4 etfs in ONE query (was N+1: 4 separate pool.acquire calls).
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT symbol, date, close FROM market_data
                    WHERE symbol = ANY($1::text[])
                    ORDER BY symbol, date DESC""",
                    ["SPY", "TLT", "UUP", "HYG", "GLD"],
                )
            # / bucket by symbol, take the most recent 30 rows per symbol
            import pandas as pd
            buckets: dict[str, list] = {}
            for r in rows:
                buckets.setdefault(r["symbol"], []).append(r)
            spy_rows = buckets.get("SPY", [])[:30]
            if not spy_rows or len(spy_rows) < 20:
                return None
            spy_df = pd.DataFrame([{"close": float(r["close"])} for r in reversed(spy_rows)])
            etf_dfs = {}
            for etf in ("TLT", "UUP", "HYG", "GLD"):
                etf_rows = buckets.get(etf, [])[:30]
                if etf_rows and len(etf_rows) >= 20:
                    etf_dfs[etf.lower()] = pd.DataFrame(
                        [{"close": float(r["close"])} for r in reversed(etf_rows)]
                    )
            im = compute_intermarket(spy_df, **etf_dfs)
            return round(im.composite, 4)

        async def _sector_rs():
            # / sector relative strength: spy + symbol over 70 days, fetched in one query
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT symbol, date, close FROM market_data
                    WHERE symbol = ANY($1::text[])
                    ORDER BY symbol, date DESC""",
                    ["SPY", symbol],
                )
            sym_sector = get_sector(symbol)
            if not sym_sector:
                return None
            import pandas as pd
            buckets: dict[str, list] = {}
            for r in rows:
                buckets.setdefault(r["symbol"], []).append(r)
            spy_rows = buckets.get("SPY", [])[:70]
            sym_rows = buckets.get(symbol, [])[:70]
            if len(spy_rows) < 60 or len(sym_rows) < 60:
                return None
            spy_close = pd.Series([float(r["close"]) for r in reversed(spy_rows)])
            sym_close = pd.Series([float(r["close"]) for r in reversed(sym_rows)])
            if len(spy_close) < 20 or len(sym_close) < 20:
                return None
            spy_ret = (spy_close.iloc[-1] / spy_close.iloc[-20]) - 1
            sym_ret = (sym_close.iloc[-1] / sym_close.iloc[-20]) - 1
            rs = sym_ret - spy_ret if spy_ret != 0 else 0.0
            return round(rs, 4)

        # / parallel-safe fetches: short_interest, dark_pool, days_to_earnings,
        # / intermarket, sector_rs always run. congressional, ratings, earnings_rev,
        # / options gated on is_etf. None entries skipped without affecting tuple shape.
        coros = [
            _safe_alt(fetch_short_interest(symbol), "short_interest"),
            _safe_alt(fetch_dark_pool_data(symbol, pool=pool), "dark_pool"),
            _safe_alt(days_to_earnings(symbol), "days_to_earnings"),
            _safe_alt(_intermarket(), "intermarket"),
            _safe_alt(_sector_rs(), "sector_rotation"),
            _safe_alt(fetch_congressional_trades(symbol), "congressional") if not is_etf else _noop(),
            _safe_alt(_ratings_with_target(), "analyst_ratings") if not is_etf else _noop(),
            _safe_alt(fetch_earnings_estimates(symbol), "earnings_revisions") if not is_etf else _noop(),
            _safe_alt(fetch_options_data(symbol), "options") if not is_etf else _noop(),
        ]
        (
            si_data, dp_data, dte, im_score, rs_score,
            cong_trades, ratings_pair, est_data, opt_data,
        ) = await asyncio.gather(*coros)

        # / map results into alt dict, preserving original keys + skip-on-None semantics
        if si_data:
            alt["short_pct_float"] = (
                si_data.get("short_percent_float")
                or si_data.get("short_ratio")
                or si_data.get("short_interest")
            )
        if dp_data:
            alt["dark_pool_ratio"] = dp_data.get("dark_pool_ratio")
        if dte is not None:
            alt["days_to_earnings"] = dte
        if im_score is not None:
            alt["intermarket_score"] = im_score
        if rs_score is not None:
            alt["sector_relative_strength"] = rs_score
        if cong_trades is not None:
            alt["congressional_buy_ratio"] = compute_net_buy_ratio(cong_trades)
        if ratings_pair is not None:
            consensus, upside = ratings_pair
            if consensus is not None:
                alt["analyst_consensus"] = consensus
            if upside is not None:
                alt["price_target_upside"] = upside
        if est_data is not None:
            alt["earnings_revision_momentum"] = compute_revision_momentum(est_data)
        if opt_data:
            alt["iv_rank"] = opt_data.get("iv_rank")
            alt["put_call_ratio"] = opt_data.get("put_call_ratio")

        return alt

    async def _analyze_equity_symbol(self, pool, symbol: str) -> float | None:
        # / run all analysis components, compute composite, store to db
        ratio_score, dcf_result, earnings_signal, insider_signal = await self._fetch_equity_components(pool, symbol)

        # / news sentiment (phase 8)
        sentiment_score: float | None = None
        try:
            sentiment_score = await compute_sentiment_score(symbol)
            if sentiment_score != 0.0:
                await store_sentiment(pool, symbol, sentiment_score)
        except Exception as exc:
            logger.warning("analyst_sentiment_failed", symbol=symbol, error=str(exc))

        regime, symbol_trend, indicator_data, sentiment_data = await self._fetch_equity_enrichment(pool, symbol)

        # / fetch alternative data sources (macro, congressional, analyst ratings, etc.)
        alt_data = await self._fetch_alternative_data(pool, symbol)

        # / fetch vix level for equity details
        vix_level: float | None = None
        try:
            async with pool.acquire() as conn:
                vix_row = await conn.fetchrow(
                    """SELECT raw_score FROM social_sentiment
                    WHERE source = 'vix'
                    ORDER BY date DESC LIMIT 1""",
                )
                if vix_row and vix_row["raw_score"] is not None:
                    # / reverse normalize: raw_vix = 30 - (normalized * 20)
                    vix_level = round(30.0 - float(vix_row["raw_score"]) * 20.0, 1)
        except Exception:
            logger.debug("vix_fetch_for_details_failed", exc_info=True)

        # / store dcf result to dcf_valuations table (regime known at this point)
        if dcf_result:
            try:
                from src.analysis.dcf_model import store_dcf_result
                await store_dcf_result(pool, dcf_result, regime=regime)
            except Exception as exc:
                logger.warning("analyst_dcf_store_failed", symbol=symbol, error=str(exc))

        # / fetch strategy positions for llm context
        strat_positions = await tools.get_strategy_positions(pool, symbol=symbol)

        # / bug e2: enrich each position with its strategy's latest quant metrics so the llm
        # / sees paper-mode performance (sharpe/sortino/win rate/maxdd) and can reason about
        # / which strategies are actually working vs which need adjustment
        if strat_positions:
            try:
                async with pool.acquire() as conn:
                    score_rows = await conn.fetch(
                        """SELECT DISTINCT ON (strategy_id) strategy_id, sharpe_ratio, sortino_ratio,
                            win_rate, max_drawdown, composite_score, total_trades, regime_breakdown
                        FROM strategy_scores
                        WHERE strategy_id = ANY($1::varchar[])
                        ORDER BY strategy_id, created_at DESC""",
                        [p["strategy_id"] for p in strat_positions],
                    )
                score_by_id = {r["strategy_id"]: dict(r) for r in score_rows}
                for p in strat_positions:
                    sc = score_by_id.get(p["strategy_id"])
                    if sc:
                        p["sharpe"] = float(sc["sharpe_ratio"]) if sc.get("sharpe_ratio") is not None else None
                        p["win_rate"] = float(sc["win_rate"]) if sc.get("win_rate") is not None else None
                        p["max_drawdown"] = float(sc["max_drawdown"]) if sc.get("max_drawdown") is not None else None
                        p["total_trades"] = int(sc["total_trades"] or 0)
            except Exception as exc:
                logger.debug("analyst_position_metrics_enrich_failed", symbol=symbol, error=str(exc)[:100])

        # / llm analysis: groq every cycle, deepseek only on hourly cycle
        regime_with_trend = regime
        if symbol_trend != "unknown":
            regime_with_trend = f"{regime} | This stock's trend: {symbol_trend} (close vs SMA50)"
        try:
            if getattr(self, "_run_deepseek", True):
                dual = await generate_dual_analysis(
                    symbol, ratio=ratio_score, dcf=dcf_result,
                    earnings=earnings_signal, insider=insider_signal, regime=regime_with_trend,
                    indicators=indicator_data, sentiment=sentiment_data,
                    positions=strat_positions,
                )
            else:
                # / groq only, skip deepseek call
                groq_only = await generate_summary(
                    symbol, ratio=ratio_score, dcf=dcf_result,
                    earnings=earnings_signal, insider=insider_signal, regime=regime_with_trend,
                    indicators=indicator_data, sentiment=sentiment_data,
                    positions=strat_positions,
                )
                from src.analysis.ai_summary import DualAnalysis
                dual = DualAnalysis(groq=groq_only, deepseek=None, consensus=groq_only.signal, consensus_confidence=groq_only.confidence)
        except Exception as exc:
            logger.warning("analyst_llm_failed", symbol=symbol, error=str(exc))
            from src.analysis.ai_summary import DualAnalysis, _build_fallback_summary
            fallback = _build_fallback_summary(symbol, ratio_score, dcf_result, earnings_signal, insider_signal)
            dual = DualAnalysis(groq=fallback, deepseek=None, consensus=fallback.signal, consensus_confidence=fallback.confidence)

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
            details["insider_signed_strength"] = insider_signal.signed_strength
        # / add dual-llm fields
        details["ai_consensus"] = dual.consensus
        details["ai_consensus_confidence"] = dual.consensus_confidence if hasattr(dual, 'consensus_confidence') else 0.0
        details["symbol_trend"] = symbol_trend
        details["llm_analysis_groq"] = dual.groq.summary
        details["llm_signal_groq"] = dual.groq.signal
        details["llm_model_groq"] = dual.groq.model_used
        if dual.deepseek:
            details["llm_analysis_deepseek"] = dual.deepseek.summary
            details["llm_signal_deepseek"] = dual.deepseek.signal
            details["llm_model_deepseek"] = dual.deepseek.model_used
        details["regime"] = regime
        if vix_level is not None:
            details["vix"] = vix_level

        # / add alternative data fields
        for key in (
            "macro_score", "congressional_buy_ratio", "analyst_consensus",
            "price_target_upside", "earnings_revision_momentum", "short_pct_float",
            "dark_pool_ratio", "iv_rank", "put_call_ratio", "days_to_earnings",
            "intermarket_score", "sector_relative_strength",
        ):
            if key in alt_data and alt_data[key] is not None:
                details[key] = alt_data[key]

        # / bug 4c: compute technical_score from indicator_data so composite != fundamental
        # / rsi midline, macd histogram sign, adx trend strength, bb position — all 0-100
        technical_score = _compute_technical_score(indicator_data)

        # / phase 6 step 8: kronos candle-sequence score (0..100), blended into composite.
        # / degrades silently to None when model is off / data too short / psutil guard trips.
        kronos_score, kronos_details = await _compute_kronos_score(pool, symbol)
        if kronos_details is not None:
            details["kronos_probability"] = kronos_details.get("probability")
            details["kronos_confidence"] = kronos_details.get("confidence")
            details["kronos_source"] = kronos_details.get("source")

        # / 70/30 blend when both fundamental+technical exist; drop to whichever side exists.
        # / then pull the composite toward kronos_score at weight _kronos_weight() (default 0.15).
        if fundamental_score is not None and technical_score is not None:
            base_composite = 0.7 * fundamental_score + 0.3 * technical_score
        elif technical_score is not None:
            base_composite = technical_score
        elif fundamental_score is not None:
            base_composite = fundamental_score
        else:
            base_composite = None

        if kronos_score is not None and base_composite is not None:
            kw = _kronos_weight()
            composite_score = round((1.0 - kw) * base_composite + kw * kronos_score, 1)
        elif base_composite is not None:
            composite_score = round(base_composite, 1)
        elif kronos_score is not None:
            composite_score = kronos_score
        else:
            composite_score = None

        # / store to analysis_scores
        used_fundamentals = ratio_score is not None or dcf_result is not None
        await tools.store_analysis_score(
            pool, symbol=symbol, as_of=date.today(),
            fundamental_score=fundamental_score,
            technical_score=technical_score,
            composite_score=composite_score,
            regime=regime,
            regime_confidence=None,
            used_fundamentals=used_fundamentals,
            details=details,
        )

        logger.info(
            "analyst_symbol_complete",
            symbol=symbol, fundamental=fundamental_score,
            technical=technical_score, composite=composite_score,
        )

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
            # / invert score for bearish signals: strong bearish = low score
            e_score = earnings.strength if earnings.signal == "bullish" else (100.0 - earnings.strength)
            components.append((e_score, 0.20))

        if insider and insider.strength is not None:
            # / invert score for bearish signals: strong selling = low score
            i_score = insider.strength if insider.signal == "bullish" else (100.0 - insider.strength)
            components.append((i_score, 0.20))

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
            # / bug e: peg=0 means unknown/divide-by-zero — never display as 0.00
            _peg = ratio.details.get("peg_ratio")
            try:
                d["peg_ratio"] = float(_peg) if _peg and float(_peg) > 0 else None
            except (TypeError, ValueError):
                d["peg_ratio"] = None
            d["fcf_margin"] = float(ratio.details.get("fcf_margin")) if ratio.details.get("fcf_margin") else None
            d["debt_to_equity"] = float(ratio.details.get("debt_to_equity")) if ratio.details.get("debt_to_equity") else None
            d["revenue_growth"] = float(ratio.details.get("revenue_growth_1y")) if ratio.details.get("revenue_growth_1y") else None
            d["sector_pe_avg"] = float(ratio.details.get("sector_pe_avg")) if ratio.details.get("sector_pe_avg") else None
            d["sector_ps_avg"] = float(ratio.details.get("sector_ps_avg")) if ratio.details.get("sector_ps_avg") else None
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
