# / per-symbol chart vision orchestration
# / fetch bars -> render chart -> gemini analyze -> ollama embed -> persist

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

from src.data.crypto_data import fetch_coin_market_chart
from src.data.market_data import fetch_bars as fetch_market_bars
from src.data.symbols import is_crypto
from src.knowledge.embedder import OllamaEmbedder
from src.vision.budget_gate import VisionBudget, release_call, reserve_call
from src.vision.chart_renderer import render_chart
from src.vision.db_helpers import _has_recent_chart_analysis, store_chart_analysis
from src.vision.gemini_client import ChartAnalysis, GeminiVisionClient

# / security: allowed chars in symbol/timeframe path segments — blocks traversal (\, /, ..)
_PATH_SAFE_RE = re.compile(r"[^A-Za-z0-9_.\-]+")

logger = structlog.get_logger(__name__)

# / freshness window — 2x/day schedule + 6h buffer prevents duplicate calls on retries
FRESHNESS_HOURS = 6

# / need at least this many bars for sma50; 120 covers ~6 months of trading days
MIN_BARS = 60
TARGET_BARS = 120


def _charts_root() -> Path:
    # / charts root is data/charts/ under project root
    return Path(__file__).resolve().parents[2] / "data" / "charts"


def _build_out_path(symbol: str, timeframe: str) -> Path:
    # / data/charts/YYYY-MM-DD/SYM_1D_HHMM.png  -- timestamp avoids overwrite on 2x/day runs
    # / security: strict regex strip on both segments blocks \ / .. null and ctrl chars
    now = datetime.now(timezone.utc)
    date_dir = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%H%M")
    safe_symbol = _PATH_SAFE_RE.sub("_", symbol).strip("._") or "unknown"
    safe_tf = _PATH_SAFE_RE.sub("_", timeframe).strip("._") or "tf"
    candidate = _charts_root() / date_dir / f"{safe_symbol}_{safe_tf}_{stamp}.png"
    # / resolve-and-check containment: reject any path that escapes the charts root
    root_resolved = _charts_root().resolve()
    candidate_resolved = candidate.resolve() if candidate.parent.exists() else candidate
    try:
        # / use relative_to() as the containment test — raises ValueError if escape
        candidate_resolved.relative_to(root_resolved)
    except ValueError:
        raise ValueError(f"chart path escape detected: {candidate!s}")
    return candidate


def _decimal_to_float(value: Any) -> float | None:
    # / asyncpg returns Decimal from market_data; mplfinance wants floats
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bars_to_df(bars: list[dict], pd_module: Any) -> Any:
    # / shared bars->dataframe path for alpaca/yfinance output shape
    rows = []
    for b in bars:
        bar_date = b.get("timestamp") or b.get("date")
        if bar_date is None:
            continue
        o = _decimal_to_float(b.get("open"))
        h = _decimal_to_float(b.get("high"))
        low = _decimal_to_float(b.get("low"))
        c = _decimal_to_float(b.get("close"))
        if None in (o, h, low, c):
            continue
        rows.append({
            "Date": pd_module.Timestamp(bar_date),
            "Open": o, "High": h, "Low": low, "Close": c,
            "Volume": float(b.get("volume") or 0),
        })
    if not rows:
        return None
    df = pd_module.DataFrame(rows).set_index("Date").sort_index()
    # / ensure datetimeindex even if Timestamps were naive
    try:
        df.index = pd_module.DatetimeIndex(df.index)
    except Exception:
        pass
    if len(df) > TARGET_BARS:
        df = df.iloc[-TARGET_BARS:]
    return df


async def _fetch_bars_df(symbol: str, timeframe: str) -> Any:
    # / returns a pandas dataframe indexed by datetime with OHLCV columns, or None
    try:
        import pandas as pd
    except ImportError:
        logger.warning("pandas_not_installed")
        return None

    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=TARGET_BARS * 2)  # / over-fetch since weekends/holidays thin equity bars

    # / alpaca covers equities + crypto pairs with the same fetch_bars signature
    bars: list[dict] = []
    try:
        bars = await fetch_market_bars(symbol, start, end)
    except Exception as exc:
        logger.warning("chart_bars_alpaca_failed", symbol=symbol, error=str(exc)[:200])
        bars = []

    if bars:
        df = _bars_to_df(bars, pd)
        if df is not None and not df.empty:
            return df

    # / crypto fallback: coingecko market_chart -> synthesize ohlc from prices
    if is_crypto(symbol):
        try:
            chart = await fetch_coin_market_chart(symbol, days=TARGET_BARS * 2)
        except Exception as exc:
            logger.warning("chart_bars_coingecko_failed", symbol=symbol, error=str(exc)[:200])
            chart = None
        if not chart or not chart.get("prices"):
            return None
        # / both arrays share the same [ms, value] shape from coingecko
        prices_df = pd.DataFrame(chart["prices"], columns=["ms", "price"])
        prices_df["price"] = prices_df["price"].astype(float)
        prices_df["timestamp"] = pd.to_datetime(prices_df["ms"], unit="ms", utc=True)
        prices_df = prices_df.set_index("timestamp").sort_index()
        daily = prices_df["price"].resample("1D").agg(["first", "max", "min", "last"]).dropna()
        daily.columns = ["Open", "High", "Low", "Close"]

        vols = chart.get("total_volumes") or []
        if vols:
            vol_df = pd.DataFrame(vols, columns=["ms", "vol"])
            vol_df["timestamp"] = pd.to_datetime(vol_df["ms"], unit="ms", utc=True)
            vol_df = vol_df.set_index("timestamp").sort_index()
            vol_daily = vol_df["vol"].resample("1D").sum().reindex(daily.index).fillna(0.0)
            daily["Volume"] = vol_daily.astype(float)
        else:
            daily["Volume"] = 0.0

        if len(daily) > TARGET_BARS:
            daily = daily.iloc[-TARGET_BARS:]
        return daily if not daily.empty else None

    return None


async def analyze_symbol_chart(
    pool,
    symbol: str,
    timeframe: str = "1D",
) -> ChartAnalysis | None:
    # / full pipeline: skip-if-fresh -> budget check -> bars -> chart -> gemini -> embed -> store
    try:
        if await _has_recent_chart_analysis(pool, symbol, timeframe, FRESHNESS_HOURS):
            logger.info(
                "chart_vision_skipped_fresh",
                symbol=symbol, timeframe=timeframe, window_hours=FRESHNESS_HOURS,
            )
            return None
    except Exception as exc:
        logger.warning("chart_freshness_check_failed", symbol=symbol, error=str(exc)[:120])

    budget = VisionBudget(pool)
    try:
        allowed = await budget.can_call_gemini(pool)
    except Exception as exc:
        logger.warning("chart_budget_check_failed", symbol=symbol, error=str(exc)[:120])
        allowed = False
    if not allowed:
        logger.info("vision_cap_reached", symbol=symbol, timeframe=timeframe)
        return None

    bars_df = await _fetch_bars_df(symbol, timeframe)
    if bars_df is None:
        logger.warning("chart_vision_no_bars", symbol=symbol, timeframe=timeframe)
        return None
    try:
        bar_count = len(bars_df)
    except Exception:
        bar_count = 0
    if bar_count < MIN_BARS:
        logger.warning(
            "chart_vision_insufficient_bars",
            symbol=symbol, timeframe=timeframe, bars=bar_count, needed=MIN_BARS,
        )
        return None

    try:
        out_path = _build_out_path(symbol, timeframe)
    except ValueError as exc:
        logger.error("chart_vision_path_rejected", symbol=symbol, error=str(exc)[:200])
        return None
    rendered = await render_chart(symbol, timeframe, bars_df, out_path)
    if rendered is None:
        logger.warning("chart_vision_render_failed", symbol=symbol, timeframe=timeframe)
        return None

    # / reserve a daily-cap slot BEFORE firing gemini; release always fires in finally
    # / prevents check-then-call races (reviewer blocker #2)
    client = GeminiVisionClient()
    await reserve_call()
    try:
        try:
            analysis = await client.analyze_chart(rendered, symbol, timeframe)
        except Exception as exc:
            logger.warning("chart_vision_gemini_failed", symbol=symbol, error=str(exc)[:200])
            return None
    finally:
        await release_call()
    if analysis is None:
        return None

    # / embed analysis text for semantic retrieval; close embedder to avoid httpx leak
    embedding: list[float] | None = None
    embedder = OllamaEmbedder()
    try:
        embedding = await embedder.embed(analysis.analysis_text)
    except Exception as exc:
        logger.info("chart_vision_embed_failed", symbol=symbol, error=str(exc)[:120])
        embedding = None
    finally:
        await embedder.close()

    try:
        await store_chart_analysis(
            pool, symbol=symbol, timeframe=timeframe,
            image_path=str(rendered), analysis=analysis, embedding=embedding,
        )
    except Exception as exc:
        logger.warning(
            "chart_vision_store_failed",
            symbol=symbol, timeframe=timeframe, error=str(exc)[:200],
        )
        return None

    return analysis
