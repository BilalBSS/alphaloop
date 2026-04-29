# / options data: yfinance options chains for iv rank, put/call ratio, max pain
# / skips crypto symbols (no options available)

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import structlog

from .symbols import is_crypto

logger = structlog.get_logger(__name__)


def _fetch_options_sync(symbol: str) -> dict[str, Any] | None:
    # / sync yfinance call — run via asyncio.to_thread
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            return None

        # / use nearest expiration for current iv and ratios
        chain = ticker.option_chain(expirations[0])
        calls = chain.calls
        puts = chain.puts

        if calls.empty and puts.empty:
            return None

        # / implied volatility from near-the-money options
        call_ivs = calls["impliedVolatility"].dropna().tolist() if not calls.empty else []
        put_ivs = puts["impliedVolatility"].dropna().tolist() if not puts.empty else []
        all_ivs = call_ivs + put_ivs
        iv_current = sum(all_ivs) / len(all_ivs) if all_ivs else None

        # / iv rank: needs historical context, approximate with current vs range in chain
        iv_rank = None
        if all_ivs and len(all_ivs) > 2:
            iv_min = min(all_ivs)
            iv_max = max(all_ivs)
            if iv_max > iv_min and iv_current is not None:
                iv_rank = (iv_current - iv_min) / (iv_max - iv_min)
                iv_rank = max(0.0, min(1.0, iv_rank))

        # / put/call ratio by volume
        call_vol = calls["volume"].sum() if not calls.empty and "volume" in calls.columns else 0
        put_vol = puts["volume"].sum() if not puts.empty and "volume" in puts.columns else 0
        put_call_ratio = None
        if call_vol and call_vol > 0:
            put_call_ratio = float(put_vol) / float(call_vol)

        # / max pain: strike price that minimizes total option premium
        max_pain = _compute_max_pain(calls, puts)

        return {
            "symbol": symbol,
            "iv_current": iv_current,
            "iv_rank": iv_rank,
            "put_call_ratio": put_call_ratio,
            "max_pain": max_pain,
        }
    except Exception as exc:
        logger.debug("options_fetch_failed", symbol=symbol, error=str(exc))
        return None


def _compute_max_pain(calls, puts) -> float | None:
    # / find strike minimizing total premium paid by option holders
    try:
        if calls.empty and puts.empty:
            return None
        strikes = set()
        if not calls.empty:
            strikes.update(calls["strike"].tolist())
        if not puts.empty:
            strikes.update(puts["strike"].tolist())
        if not strikes:
            return None

        min_pain = float("inf")
        max_pain_strike = None

        for strike in sorted(strikes):
            total_pain = 0.0
            # / call holders lose when price < strike
            if not calls.empty:
                for _, row in calls.iterrows():
                    oi = row.get("openInterest", 0) or 0
                    call_strike = row.get("strike", 0)
                    if strike > call_strike:
                        total_pain += (strike - call_strike) * oi
            # / put holders lose when price > strike
            if not puts.empty:
                for _, row in puts.iterrows():
                    oi = row.get("openInterest", 0) or 0
                    put_strike = row.get("strike", 0)
                    if strike < put_strike:
                        total_pain += (put_strike - strike) * oi

            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = strike

        return max_pain_strike
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


async def fetch_options_data(symbol: str) -> dict[str, Any] | None:
    if is_crypto(symbol):
        return None
    try:
        result = await asyncio.to_thread(_fetch_options_sync, symbol)
        if result:
            logger.info("options_data_fetched", symbol=symbol, iv_rank=result.get("iv_rank"))
        return result
    except Exception as exc:
        logger.debug("options_data_failed", symbol=symbol, error=str(exc))
        return None


async def store_options_data(pool: Any, data: dict[str, Any]) -> None:
    if not data:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO options_data (symbol, date, iv_current, iv_rank, put_call_ratio, max_pain)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (symbol, date) DO UPDATE SET
                iv_current = EXCLUDED.iv_current, iv_rank = EXCLUDED.iv_rank,
                put_call_ratio = EXCLUDED.put_call_ratio, max_pain = EXCLUDED.max_pain
            """,
            data["symbol"], date.today(),
            data.get("iv_current"), data.get("iv_rank"),
            data.get("put_call_ratio"), data.get("max_pain"),
        )
