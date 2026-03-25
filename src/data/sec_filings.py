# / edgartools form 4 insider trades + sec filings
# / sync library wrapped in asyncio.to_thread
# / global semaphore enforces 10 req/s across all concurrent calls
# / graceful: logs parse failures to data_quality, continues with next symbol

from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# / sec edgar: 10 req/sec max — global semaphore prevents concurrent overflow
_edgar_semaphore = asyncio.Semaphore(1)  # serialize all edgar requests
_edgar_delay = 0.15  # 150ms between requests (safe margin under 10/sec)


def _get_user_agent() -> str:
    return os.environ.get("SEC_EDGAR_USER_AGENT", "QuantTrader quant@example.com")


def _fetch_insider_trades_sync(
    symbol: str,
    days: int = 90,
) -> list[dict[str, Any]]:
    # / sync edgartools fetch — run via asyncio.to_thread
    try:
        from edgar import Company
    except ImportError:
        logger.warning("edgartools_not_installed")
        return []

    try:
        os.environ.setdefault("EDGAR_IDENTITY", _get_user_agent())
        company = Company(symbol)
        filings = company.get_filings(form="4")

        if filings is None or len(filings) == 0:
            logger.info("no_form4_filings", symbol=symbol)
            return []

        cutoff = date.today() - timedelta(days=days)
        trades: list[dict[str, Any]] = []

        # / iterate recent filings
        for filing in filings[:20]:  # cap at 20 most recent
            try:
                filing_date = filing.filing_date
                if hasattr(filing_date, "date"):
                    filing_date = filing_date.date()

                if filing_date < cutoff:
                    break

                # / parse the form 4 xml
                form4 = filing.obj()
                if form4 is None:
                    continue

                # / extract transactions
                owner_name = _safe_get(form4, "owner_name", "Unknown")
                owner_title = _safe_get(form4, "owner_title", "")

                for txn in _get_transactions(form4):
                    trades.append({
                        "symbol": symbol,
                        "filing_date": filing_date,
                        "insider_name": str(owner_name)[:200],
                        "insider_title": str(owner_title)[:100],
                        "transaction_type": txn.get("type", "unknown"),
                        "shares": Decimal(str(txn.get("shares", 0))),
                        "price_per_share": Decimal(str(txn.get("price", 0))),
                        "total_value": Decimal(str(txn.get("shares", 0))) * Decimal(str(txn.get("price", 0))),
                    })

            except Exception as exc:
                logger.warning(
                    "form4_parse_error",
                    symbol=symbol,
                    filing=str(getattr(filing, "accession_no", "unknown")),
                    error=str(exc),
                )
                continue

        return trades

    except Exception as exc:
        logger.warning("edgartools_fetch_error", symbol=symbol, error=str(exc))
        return []


def _safe_get(obj: Any, attr: str, default: Any = None) -> Any:
    # / safely get attribute from edgartools object
    try:
        val = getattr(obj, attr, default)
        return val if val is not None else default
    except Exception:
        return default


def _get_transactions(form4: Any) -> list[dict[str, Any]]:
    # / extract buy/sell transactions from form4 object
    txns: list[dict[str, Any]] = []

    # / edgartools form4 may expose transactions differently across versions
    for attr in ("non_derivative_transactions", "transactions", "derivative_transactions"):
        items = getattr(form4, attr, None)
        if items is None:
            continue

        try:
            for item in items:
                txn_code = _safe_get(item, "transaction_code", "")
                shares = _safe_get(item, "shares", 0) or _safe_get(item, "transaction_shares", 0)
                price = _safe_get(item, "price_per_share", 0) or _safe_get(item, "transaction_price_per_share", 0)

                if txn_code in ("P", "p"):
                    txn_type = "buy"
                elif txn_code in ("S", "s"):
                    txn_type = "sell"
                elif txn_code in ("M", "m"):
                    txn_type = "option_exercise"
                else:
                    txn_type = txn_code or "unknown"

                txns.append({
                    "type": txn_type,
                    "shares": float(shares) if shares else 0,
                    "price": float(price) if price else 0,
                })
        except Exception:
            continue

    return txns


async def fetch_insider_trades(
    symbol: str,
    days: int = 90,
) -> list[dict[str, Any]]:
    # / async wrapper with global rate limiting
    async with _edgar_semaphore:
        await asyncio.sleep(_edgar_delay)
        try:
            trades = await asyncio.to_thread(_fetch_insider_trades_sync, symbol, days)
            logger.info("fetched_insider_trades", symbol=symbol, count=len(trades))
            return trades
        except Exception as exc:
            logger.warning("insider_trades_failed", symbol=symbol, error=str(exc))
            return []


async def fetch_all_insider_trades(
    symbols: list[str],
    days: int = 90,
) -> list[dict[str, Any]]:
    # / fetch insider trades for all symbols sequentially (rate limited)
    all_trades: list[dict[str, Any]] = []
    for symbol in symbols:
        trades = await fetch_insider_trades(symbol, days)
        all_trades.extend(trades)
    return all_trades


async def store_insider_trades(pool, trades: list[dict[str, Any]]) -> int:
    # / insert insider trades, handle duplicates
    if not trades:
        return 0

    async with pool.acquire() as conn:
        inserted = 0
        for t in trades:
            try:
                await conn.execute(
                    """
                    INSERT INTO insider_trades (
                        symbol, filing_date, insider_name, insider_title,
                        transaction_type, shares, price_per_share, total_value
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                    ON CONFLICT (symbol, filing_date, insider_name, transaction_type, shares, price_per_share)
                    DO NOTHING
                    """,
                    t["symbol"], t["filing_date"], t["insider_name"],
                    t["insider_title"], t["transaction_type"],
                    t["shares"], t["price_per_share"], t["total_value"],
                )
                inserted += 1
            except Exception as exc:
                logger.warning(
                    "insider_trade_insert_failed",
                    symbol=t["symbol"],
                    error=str(exc),
                )

        logger.info("stored_insider_trades", count=inserted)
        return inserted


async def log_data_quality_issue(pool, symbol: str, details: str) -> None:
    # / log parsing/fetch failures to data_quality table
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO data_quality (source, symbol, date, issue_type, details)
                VALUES ('sec_filings', $1, $2, 'api_error', $3)
                """,
                symbol, date.today(), details,
            )
    except Exception:
        pass  # don't fail on logging
