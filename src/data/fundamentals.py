# / yfinance fundamentals: p/e, p/s, revenue growth, fcf margin, sector averages
# / sync library wrapped in asyncio.to_thread for async compat
# / graceful: returns partial data when fields missing, warns but doesn't crash

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from .validators import validate_fundamentals

logger = structlog.get_logger(__name__)


def _safe_decimal(value: Any) -> Decimal | None:
    # / safely convert to decimal, return none on failure
    if value is None:
        return None
    try:
        d = Decimal(str(value))
        if not d.is_finite():
            return None
        return d
    except (InvalidOperation, ValueError, TypeError):
        return None


def _fetch_yfinance(symbol: str) -> dict[str, Any] | None:
    # / sync yfinance fetch — run via asyncio.to_thread
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance_not_installed")
        return None

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        if not info or info.get("regularMarketPrice") is None:
            logger.warning("yfinance_no_data", symbol=symbol)
            return None

        return {
            "symbol": symbol,
            "date": date.today(),
            "pe_ratio": _safe_decimal(info.get("trailingPE")),
            "pe_forward": _safe_decimal(info.get("forwardPE")),
            "ps_ratio": _safe_decimal(info.get("priceToSalesTrailing12Months")),
            "peg_ratio": _safe_decimal(info.get("pegRatio")),
            "revenue_growth_1y": _safe_decimal(info.get("revenueGrowth")),
            "revenue_growth_3y": None,  # yfinance doesn't provide multi-year directly
            "fcf_margin": _compute_fcf_margin(info),
            "debt_to_equity": _safe_decimal(info.get("debtToEquity")),
            "sector": info.get("sector", "Unknown"),
            "sector_pe_avg": None,  # computed across universe, not per-ticker
            "sector_ps_avg": None,
        }
    except Exception as exc:
        logger.warning("yfinance_fetch_error", symbol=symbol, error=str(exc))
        return None


def _compute_fcf_margin(info: dict) -> Decimal | None:
    # / fcf margin = free cash flow / total revenue
    fcf = info.get("freeCashflow")
    revenue = info.get("totalRevenue")
    if fcf is not None and revenue and revenue != 0:
        return _safe_decimal(fcf / revenue)
    return None


async def fetch_fundamentals(symbol: str) -> dict[str, Any] | None:
    # / async wrapper for yfinance fundamentals fetch
    try:
        result = await asyncio.to_thread(_fetch_yfinance, symbol)
        if result:
            logger.info("fetched_fundamentals", symbol=symbol)
        return result
    except Exception as exc:
        logger.warning("fundamentals_fetch_failed", symbol=symbol, error=str(exc))
        return None


async def fetch_all_fundamentals(
    symbols: list[str],
) -> list[dict[str, Any]]:
    # / fetch fundamentals for all symbols, compute sector averages after
    results: list[dict[str, Any]] = []

    for symbol in symbols:
        data = await fetch_fundamentals(symbol)
        if data:
            results.append(data)
        # / 1 req/sec rate limit for yfinance
        await asyncio.sleep(1.0)

    # / compute sector averages
    if results:
        _compute_sector_averages(results)

    return results


def _compute_sector_averages(data: list[dict[str, Any]]) -> None:
    # / fill in sector_pe_avg and sector_ps_avg across the universe
    sectors: dict[str, list[dict[str, Any]]] = {}
    for d in data:
        sector = d.get("sector", "Unknown")
        sectors.setdefault(sector, []).append(d)

    for sector, items in sectors.items():
        pe_values = [d["pe_ratio"] for d in items if d.get("pe_ratio") is not None]
        ps_values = [d["ps_ratio"] for d in items if d.get("ps_ratio") is not None]

        avg_pe = sum(pe_values) / len(pe_values) if pe_values else None
        avg_ps = sum(ps_values) / len(ps_values) if ps_values else None

        for d in items:
            d["sector_pe_avg"] = avg_pe
            d["sector_ps_avg"] = avg_ps


async def store_fundamentals(pool, data: list[dict[str, Any]]) -> int:
    # / validate and insert fundamentals, handle duplicates
    if not data:
        return 0

    valid = []
    for d in data:
        results = validate_fundamentals(d)
        if all(r.valid for r in results):
            valid.append(d)
        else:
            invalid = [r for r in results if not r.valid]
            logger.warning(
                "fundamentals_validation_failed",
                symbol=d.get("symbol"),
                reasons=[r.reason for r in invalid],
            )

    if not valid:
        return 0

    async with pool.acquire() as conn:
        inserted = 0
        for d in valid:
            try:
                await conn.execute(
                    """
                    INSERT INTO fundamentals (
                        symbol, date, pe_ratio, pe_forward, ps_ratio, peg_ratio,
                        revenue_growth_1y, revenue_growth_3y, fcf_margin,
                        debt_to_equity, sector, sector_pe_avg, sector_ps_avg
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                    ON CONFLICT (symbol, date) DO UPDATE SET
                        pe_ratio = EXCLUDED.pe_ratio,
                        pe_forward = EXCLUDED.pe_forward,
                        ps_ratio = EXCLUDED.ps_ratio,
                        peg_ratio = EXCLUDED.peg_ratio,
                        revenue_growth_1y = EXCLUDED.revenue_growth_1y,
                        revenue_growth_3y = EXCLUDED.revenue_growth_3y,
                        fcf_margin = EXCLUDED.fcf_margin,
                        debt_to_equity = EXCLUDED.debt_to_equity,
                        sector = EXCLUDED.sector,
                        sector_pe_avg = EXCLUDED.sector_pe_avg,
                        sector_ps_avg = EXCLUDED.sector_ps_avg
                    """,
                    d["symbol"], d["date"], d.get("pe_ratio"), d.get("pe_forward"),
                    d.get("ps_ratio"), d.get("peg_ratio"), d.get("revenue_growth_1y"),
                    d.get("revenue_growth_3y"), d.get("fcf_margin"),
                    d.get("debt_to_equity"), d.get("sector", "Unknown"),
                    d.get("sector_pe_avg"), d.get("sector_ps_avg"),
                )
                inserted += 1
            except Exception as exc:
                logger.warning(
                    "fundamentals_insert_failed",
                    symbol=d["symbol"],
                    error=str(exc),
                )

        logger.info("stored_fundamentals", count=inserted)
        return inserted
