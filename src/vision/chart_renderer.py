# / render candlestick charts via mplfinance for gemini vision input
# / raw candles + volume + sma20/sma50 overlays, no s/r lines

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _render_sync(bars_df: Any, out_path: Path, symbol: str, timeframe: str) -> Path | None:
    # / sync mplfinance call — run inside asyncio.to_thread
    try:
        import mplfinance as mpf
    except ImportError:
        logger.warning("mplfinance_not_installed")
        return None

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        mpf.plot(
            bars_df,
            type="candle",
            volume=True,
            mav=(20, 50),
            style="charles",
            title=f"{symbol} {timeframe}",
            savefig=dict(fname=str(out_path), dpi=120, bbox_inches="tight"),
        )
        return out_path
    except Exception as exc:
        logger.warning(
            "chart_render_failed",
            symbol=symbol, timeframe=timeframe, path=str(out_path), error=str(exc)[:200],
        )
        return None


async def render_chart(
    symbol: str,
    timeframe: str,
    bars_df: Any,
    out_path: Path,
) -> Path | None:
    # / render an ohlcv dataframe to a png chart; returns path or None on failure
    if bars_df is None:
        logger.info("chart_render_skipped_no_df", symbol=symbol, timeframe=timeframe)
        return None
    try:
        if getattr(bars_df, "empty", True):
            logger.info("chart_render_skipped_empty_df", symbol=symbol, timeframe=timeframe)
            return None
    except Exception:
        return None

    try:
        result = await asyncio.to_thread(_render_sync, bars_df, out_path, symbol, timeframe)
    except Exception as exc:
        logger.warning(
            "chart_render_thread_failed",
            symbol=symbol, timeframe=timeframe, error=str(exc)[:200],
        )
        return None

    if result and result.exists() and result.stat().st_size > 0:
        logger.info("chart_rendered", symbol=symbol, timeframe=timeframe, path=str(result))
        return result
    return None
