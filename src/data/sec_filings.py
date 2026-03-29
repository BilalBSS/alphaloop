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
                owner_name = _safe_get(form4, "insider_name", "Unknown")
                owner_title = _safe_get(form4, "position", "")

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


def _to_float(val: Any) -> float:
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


def _code_to_type(code: str) -> str:
    # / sec form 4 transaction codes
    _MAP = {
        "P": "buy",
        "S": "sell",
        "M": "option_exercise",
        "A": "grant",
        "C": "conversion",
        "D": "disposition",
        "F": "tax_payment",
        "G": "gift",
        "I": "discretionary",
        "J": "other",
        "W": "will_exercise",
        "X": "exercise",
    }
    return _MAP.get((code or "").upper(), "other")


def _get_transactions(form4: Any) -> list[dict[str, Any]]:
    # / extract buy/sell transactions from edgartools v5 form4 object
    txns: list[dict[str, Any]] = []

    # / v5: market_trades is a dataframe of open market buys/sells
    try:
        trades_df = getattr(form4, "market_trades", None)
        if trades_df is not None and not trades_df.empty:
            for _, row in trades_df.iterrows():
                txns.append({
                    "type": _code_to_type(str(row.get("Code", ""))),
                    "shares": _to_float(row.get("Shares", 0)),
                    "price": _to_float(row.get("Price", 0)),
                })
    except Exception:
        pass

    # / v5: non-derivative table for option exercises, gifts, etc
    try:
        ndt = getattr(form4, "non_derivative_table", None)
        if ndt is not None and getattr(ndt, "has_transactions", False):
            df = ndt.transactions.data
            non_market = df[~df["Code"].isin(["P", "S", "p", "s"])]
            for _, row in non_market.iterrows():
                txns.append({
                    "type": _code_to_type(str(row.get("Code", ""))),
                    "shares": _to_float(row.get("Shares", 0)),
                    "price": _to_float(row.get("Price", 0)),
                })
    except Exception:
        pass

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
