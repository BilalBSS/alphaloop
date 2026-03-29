#!/usr/bin/env python3
# / backfill script: market_data -> regimes -> fundamentals -> insider trades
# / runs each step independently — if one fails, the rest still run
# / usage: python -m scripts.backfill [--years 5] [--symbols SPY,AAPL,BTC-USD]

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# / add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import structlog

from src.data.db import init_db, close_db
from src.data.symbols import EQUITY_UNIVERSE, CRYPTO_UNIVERSE, FULL_UNIVERSE, is_crypto

logger = structlog.get_logger(__name__)


async def run_backfill(symbols: list[str], years: int) -> None:
    pool = await init_db()

    try:
        await _run_steps(pool, symbols, years)
    finally:
        await close_db()
    logger.info("backfill_finished")


async def _run_steps(pool, symbols: list[str], years: int) -> None:
    equity_symbols = [s for s in symbols if not is_crypto(s)]
    crypto_symbols = [s for s in symbols if is_crypto(s)]

    # / step 1: market data
    logger.info("step_1_market_data", symbols=symbols)
    try:
        from src.data.market_data import backfill
        results = await backfill(pool, symbols, years=years)
        total_bars = sum(results.values())
        logger.info("step_1_complete", total_bars=total_bars, per_symbol=results)
    except Exception as exc:
        logger.error("step_1_failed", error=str(exc))

    # / step 2: regime detection (needs market_data from step 1)
    logger.info("step_2_regime_detection")
    try:
        from src.data.regime_detector import backfill_regimes

        if equity_symbols:
            # / spy is the index for equity regimes
            spy_symbol = "SPY" if "SPY" in symbols else equity_symbols[0]
            equity_count = await backfill_regimes(pool, index_symbol=spy_symbol, market="equity")
            logger.info("step_2_equity_complete", count=equity_count)

        if crypto_symbols:
            btc_symbol = "BTC-USD" if "BTC-USD" in symbols else crypto_symbols[0]
            crypto_count = await backfill_regimes(pool, index_symbol=btc_symbol, market="crypto")
            logger.info("step_2_crypto_complete", count=crypto_count)
    except Exception as exc:
        logger.error("step_2_failed", error=str(exc))

    # / step 3: fundamentals (equities only, crypto has no yfinance fundamentals)
    if equity_symbols:
        logger.info("step_3_fundamentals", symbols=equity_symbols)
        try:
            from src.data.fundamentals import fetch_all_fundamentals, store_fundamentals
            data = await fetch_all_fundamentals(equity_symbols)
            count = await store_fundamentals(pool, data)
            logger.info("step_3_complete", count=count)
        except Exception as exc:
            logger.error("step_3_failed", error=str(exc))

    # / step 4: insider trades (equities only)
    if equity_symbols:
        logger.info("step_4_insider_trades", symbols=equity_symbols)
        try:
            from src.data.sec_filings import fetch_all_insider_trades, store_insider_trades
            trades = await fetch_all_insider_trades(equity_symbols, days=90)
            count = await store_insider_trades(pool, trades)
            logger.info("step_4_complete", count=count)
        except Exception as exc:
            logger.error("step_4_failed", error=str(exc))


def main() -> None:
    parser = argparse.ArgumentParser(description="backfill market data")
    parser.add_argument("--years", type=int, default=5, help="years of history (default: 5)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="comma-separated symbols (default: full universe)")
    args = parser.parse_args()

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        symbols = FULL_UNIVERSE

    print(f"backfilling {len(symbols)} symbols, {args.years} years")
    print(f"symbols: {symbols}")
    asyncio.run(run_backfill(symbols, args.years))


if __name__ == "__main__":
    main()
