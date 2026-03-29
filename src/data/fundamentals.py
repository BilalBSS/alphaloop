# / fundamentals: edgar (primary) → finnhub (fallback) → yfinance (last resort)
# / edgar provides authoritative financial data from sec 10-K/10-Q filings
# / finnhub fills gaps with real-time ratios, yfinance as final fallback
# / graceful: returns partial data when fields missing, warns but doesn't crash

from __future__ import annotations

import asyncio
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from .validators import validate_fundamentals

logger = structlog.get_logger(__name__)

# / reuse sec_filings semaphore for edgar rate limiting
from .sec_filings import _edgar_semaphore, _edgar_delay


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


def _pct_to_dec(value: Any) -> float | None:
    # / finnhub returns some metrics as percentages (18.5 = 18.5%), convert to decimal (0.185)
    if value is None:
        return None
    try:
        return float(value) / 100.0
    except (ValueError, TypeError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


# / --- edgar fetcher (primary) ---

def _fetch_edgar_sync(symbol: str) -> dict[str, Any] | None:
    # / edgartools v5: use Financials class with standardized getters
    try:
        from edgar import Company
    except ImportError:
        logger.warning("edgartools_not_installed")
        return None

    try:
        os.environ.setdefault("EDGAR_IDENTITY", os.environ.get("SEC_EDGAR_USER_AGENT", "QuantTrader quant@example.com"))
        company = Company(symbol)
        financials = company.get_financials()
        if financials is None:
            logger.info("edgar_no_financials", symbol=symbol)
            return None

        # / standardized getters handle xbrl tag differences across companies
        revenue = _fin_val(financials.get_revenue())
        if not revenue:
            logger.info("edgar_no_revenue", symbol=symbol)
            return None

        prev_revenue = _fin_val(financials.get_revenue(1))
        operating_cf = _fin_val(financials.get_operating_cash_flow())
        capex = _fin_val(financials.get_capital_expenditures())
        fcf_val = _fin_val(financials.get_free_cash_flow())
        shares = _fin_val(financials.get_shares_outstanding_basic())
        total_equity = _fin_val(financials.get_stockholders_equity())
        total_liabilities = _fin_val(financials.get_total_liabilities())
        net_income = _fin_val(financials.get_net_income())
        current_assets = _fin_val(financials.get_current_assets())
        total_cash = current_assets  # / best available proxy, no dedicated cash getter

        # / xbrl fallback for shares_outstanding when standardized getter returns None
        if shares is None:
            try:
                facts = company.get_facts()
                for concept in ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding", "CommonStockSharesIssued"):
                    fact = facts.get(f"us-gaap:{concept}") if facts else None
                    if fact is not None:
                        shares = _fin_val(fact)
                        if shares:
                            break
            except Exception:
                pass

        # / compute derived fields
        if fcf_val is None and operating_cf is not None and capex is not None:
            fcf_val = operating_cf - abs(capex)
        if fcf_val is None and net_income is not None and capex is not None:
            fcf_val = net_income - abs(capex)
        fcf_margin = fcf_val / revenue if (fcf_val is not None and revenue > 0) else None
        rev_growth = (revenue - prev_revenue) / abs(prev_revenue) if (prev_revenue and abs(prev_revenue) > 0) else None

        # / estimate debt from liabilities - equity (no dedicated getter for total debt)
        total_debt_est = (total_liabilities - total_equity) if (total_liabilities and total_equity) else None
        # / net debt = total debt - cash
        net_debt_val = (total_debt_est - total_cash) if (total_debt_est is not None and total_cash is not None) else total_debt_est

        # / debt to equity
        de_ratio = total_liabilities / total_equity if (total_liabilities and total_equity and total_equity > 0) else None

        # / sector from company sic description
        sector = getattr(company, "sic_description", None) or "Unknown"
        sector_lower = sector.lower()
        if any(k in sector_lower for k in ("software", "computer", "semiconductor", "electronic")):
            sector = "Technology"
        elif any(k in sector_lower for k in ("pharma", "biotech", "medical", "drug")):
            sector = "Healthcare"
        elif any(k in sector_lower for k in ("retail", "consumer")):
            sector = "Consumer Cyclical"

        result = {
            "symbol": symbol,
            "date": date.today(),
            "pe_ratio": None,  # / needs current market price
            "pe_forward": None,
            "ps_ratio": None,
            "peg_ratio": None,
            "revenue_growth_1y": _safe_decimal(rev_growth),
            "revenue_growth_3y": None,
            "fcf_margin": _safe_decimal(fcf_margin),
            "debt_to_equity": _safe_decimal(de_ratio),
            "sector": sector,
            "sector_pe_avg": None,
            "sector_ps_avg": None,
            "total_revenue": _safe_decimal(revenue),
            "net_income": _safe_decimal(net_income),
            "free_cash_flow": _safe_decimal(fcf_val),
            "total_debt": _safe_decimal(total_debt_est),
            "total_cash": _safe_decimal(total_cash),
            "shares_outstanding": int(shares) if shares else None,
            "net_debt": _safe_decimal(net_debt_val),
            "data_source": "edgar",
        }
        logger.info("edgar_fundamentals_fetched", symbol=symbol, revenue=revenue, shares=shares)
        return result

    except Exception as exc:
        logger.info("edgar_fetch_failed", symbol=symbol, error=str(exc)[:150])
        return None


def _fin_val(metric: Any) -> float | None:
    # / extract numeric value from edgartools financial metric object
    if metric is None:
        return None
    try:
        if hasattr(metric, "value"):
            v = metric.value
            return float(v) if v is not None else None
        return float(metric)
    except (ValueError, TypeError):
        return None


# / --- finnhub fetcher (fallback) ---

async def _fetch_finnhub(symbol: str) -> dict[str, Any] | None:
    # / finnhub basic-financials + company profile
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return None

    try:
        import httpx
        headers = {"X-Finnhub-Token": key}
        async with httpx.AsyncClient(timeout=10.0) as client:
            # / basic financials
            resp = await client.get(
                f"https://finnhub.io/api/v1/stock/metric",
                params={"symbol": symbol, "metric": "all"},
                headers=headers,
            )
            if resp.status_code != 200:
                return None
            metrics = resp.json().get("metric", {})

            # / company profile for sector + shares
            resp2 = await client.get(
                f"https://finnhub.io/api/v1/stock/profile2",
                params={"symbol": symbol},
                headers=headers,
            )
            profile = resp2.json() if resp2.status_code == 200 else {}

        if not metrics:
            return None

        shares = profile.get("shareOutstanding")
        # / finnhub returns shares in millions
        shares_int = int(shares * 1_000_000) if shares else None
        mcap = profile.get("marketCapitalization")
        mcap_val = mcap * 1_000_000 if mcap else None

        # / compute revenue from P/S if available
        ps = _safe_float(metrics.get("psTTM"))
        total_rev = (mcap_val / ps) if (mcap_val and ps and ps > 0) else None

        result = {
            "symbol": symbol,
            "date": date.today(),
            "pe_ratio": _safe_decimal(metrics.get("peTTM")),
            "pe_forward": _safe_decimal(metrics.get("peAnnual")),
            "ps_ratio": _safe_decimal(metrics.get("psTTM")),
            "peg_ratio": _safe_decimal(metrics.get("pegAnnual")),
            # / finnhub returns growth as percentages (18.5 = 18.5%), normalize to decimal (0.185)
            "revenue_growth_1y": _safe_decimal(_pct_to_dec(metrics.get("revenueGrowthQuarterlyYoy"))),
            "revenue_growth_3y": _safe_decimal(_pct_to_dec(metrics.get("revenueGrowth3Y"))),
            "fcf_margin": _safe_decimal(_pct_to_dec(metrics.get("fcfMarginTTM"))),
            "debt_to_equity": _safe_decimal(metrics.get("totalDebt/totalEquityQuarterly")),
            "sector": profile.get("finnhubIndustry", "Unknown"),
            "sector_pe_avg": None,
            "sector_ps_avg": None,
            "total_revenue": _safe_decimal(total_rev),
            "shares_outstanding": shares_int,
            "total_debt": _safe_decimal(metrics.get("totalDebtMRQ")),
            "total_cash": _safe_decimal(metrics.get("cashPerShareQuarterly") * shares if (metrics.get("cashPerShareQuarterly") and shares) else None),
            "free_cash_flow": _safe_decimal(metrics.get("freeCashFlowTTM")),
            "net_debt": _safe_decimal(
                (metrics.get("totalDebtMRQ") or 0) - (metrics.get("cashPerShareQuarterly", 0) * shares if (metrics.get("cashPerShareQuarterly") and shares) else 0)
                if metrics.get("totalDebtMRQ") else None
            ),
            "data_source": "finnhub",
        }
        logger.info("finnhub_fundamentals_fetched", symbol=symbol)
        return result

    except Exception as exc:
        logger.info("finnhub_fundamentals_failed", symbol=symbol, error=str(exc)[:100])
        return None


def _compute_fcf_margin(info: dict) -> Decimal | None:
    # / fcf margin = free cash flow / total revenue
    fcf = info.get("freeCashflow")
    revenue = info.get("totalRevenue")
    if fcf is not None and revenue and revenue != 0:
        return _safe_decimal(fcf / revenue)
    return None


# / --- yfinance fetcher (last resort) ---

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

        fcf = info.get("freeCashflow")
        revenue = info.get("totalRevenue")
        fcf_margin = _compute_fcf_margin(info)
        total_debt = _safe_float(info.get("totalDebt"))
        total_cash = _safe_float(info.get("totalCash"))
        shares = info.get("sharesOutstanding")

        return {
            "symbol": symbol,
            "date": date.today(),
            "pe_ratio": _safe_decimal(info.get("trailingPE")),
            "pe_forward": _safe_decimal(info.get("forwardPE")),
            "ps_ratio": _safe_decimal(info.get("priceToSalesTrailing12Months")),
            "peg_ratio": _safe_decimal(info.get("pegRatio")),
            "revenue_growth_1y": _safe_decimal(info.get("revenueGrowth")),
            "revenue_growth_3y": None,
            "fcf_margin": fcf_margin,
            "debt_to_equity": _safe_decimal(info.get("debtToEquity")),
            "sector": info.get("sector", "Unknown"),
            "sector_pe_avg": None,
            "sector_ps_avg": None,
            "total_revenue": _safe_decimal(revenue),
            "free_cash_flow": _safe_decimal(fcf),
            "total_debt": _safe_decimal(total_debt),
            "total_cash": _safe_decimal(total_cash),
            "shares_outstanding": int(shares) if shares else None,
            "net_debt": _safe_decimal(total_debt - total_cash) if (total_debt is not None and total_cash is not None) else None,
            "data_source": "yfinance",
        }
    except Exception as exc:
        logger.warning("yfinance_fetch_error", symbol=symbol, error=str(exc))
        return None


# / --- fetch chain: edgar → finnhub → yfinance ---

async def fetch_fundamentals(symbol: str) -> dict[str, Any] | None:
    # / try edgar first (authoritative, quarterly filings)
    edgar_result = None
    async with _edgar_semaphore:
        await asyncio.sleep(_edgar_delay)
        try:
            edgar_result = await asyncio.to_thread(_fetch_edgar_sync, symbol)
        except Exception as exc:
            logger.info("edgar_fundamentals_error", symbol=symbol, error=str(exc)[:100])

    # / always fetch finnhub for price ratios (P/E, P/S, PEG) + sector
    finnhub_result = await _fetch_finnhub(symbol)

    if edgar_result and finnhub_result:
        # / merge: edgar financials + finnhub price ratios
        for key in ("pe_ratio", "pe_forward", "ps_ratio", "peg_ratio", "sector",
                     "revenue_growth_1y", "debt_to_equity"):
            if edgar_result.get(key) is None and finnhub_result.get(key) is not None:
                edgar_result[key] = finnhub_result[key]
        # / prefer finnhub shares if edgar didn't get them
        if edgar_result.get("shares_outstanding") is None:
            edgar_result["shares_outstanding"] = finnhub_result.get("shares_outstanding")
        return edgar_result

    if edgar_result:
        return edgar_result

    if finnhub_result:
        return finnhub_result

    # / yfinance last resort
    try:
        result = await asyncio.to_thread(_fetch_yfinance, symbol)
        if result:
            logger.info("fetched_fundamentals_yfinance", symbol=symbol)
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
        await asyncio.sleep(0.5)

    # / compute sector averages
    if results:
        _compute_sector_averages(results)

    return results


def _compute_sector_averages(data: list[dict[str, Any]]) -> None:
    # / fill in sector averages across the universe
    sectors: dict[str, list[dict[str, Any]]] = {}
    for d in data:
        sector = d.get("sector", "Unknown")
        sectors.setdefault(sector, []).append(d)

    for sector, items in sectors.items():
        pe_values = [d["pe_ratio"] for d in items if d.get("pe_ratio") is not None]
        ps_values = [d["ps_ratio"] for d in items if d.get("ps_ratio") is not None]
        fcf_values = [d["fcf_margin"] for d in items if d.get("fcf_margin") is not None]
        de_values = [d["debt_to_equity"] for d in items if d.get("debt_to_equity") is not None]
        rev_values = [d["revenue_growth_1y"] for d in items if d.get("revenue_growth_1y") is not None]

        avg_pe = sum(pe_values) / len(pe_values) if pe_values else None
        avg_ps = sum(ps_values) / len(ps_values) if ps_values else None
        avg_fcf = sum(fcf_values) / len(fcf_values) if fcf_values else None
        avg_de = sum(de_values) / len(de_values) if de_values else None
        avg_rev = sum(rev_values) / len(rev_values) if rev_values else None

        for d in items:
            d["sector_pe_avg"] = avg_pe
            d["sector_ps_avg"] = avg_ps
            d["sector_fcf_margin_avg"] = avg_fcf
            d["sector_de_avg"] = avg_de
            d["sector_rev_growth_avg"] = avg_rev


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
                        debt_to_equity, sector, sector_pe_avg, sector_ps_avg,
                        sector_fcf_margin_avg, sector_de_avg, sector_rev_growth_avg,
                        shares_outstanding, net_debt, total_revenue, free_cash_flow,
                        total_debt, total_cash, net_income, data_source
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24)
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
                        sector_ps_avg = EXCLUDED.sector_ps_avg,
                        sector_fcf_margin_avg = EXCLUDED.sector_fcf_margin_avg,
                        sector_de_avg = EXCLUDED.sector_de_avg,
                        sector_rev_growth_avg = EXCLUDED.sector_rev_growth_avg,
                        shares_outstanding = EXCLUDED.shares_outstanding,
                        net_debt = EXCLUDED.net_debt,
                        total_revenue = EXCLUDED.total_revenue,
                        free_cash_flow = EXCLUDED.free_cash_flow,
                        total_debt = EXCLUDED.total_debt,
                        total_cash = EXCLUDED.total_cash,
                        net_income = EXCLUDED.net_income,
                        data_source = EXCLUDED.data_source
                    """,
                    d["symbol"], d["date"], d.get("pe_ratio"), d.get("pe_forward"),
                    d.get("ps_ratio"), d.get("peg_ratio"), d.get("revenue_growth_1y"),
                    d.get("revenue_growth_3y"), d.get("fcf_margin"),
                    d.get("debt_to_equity"), d.get("sector", "Unknown"),
                    d.get("sector_pe_avg"), d.get("sector_ps_avg"),
                    d.get("sector_fcf_margin_avg"), d.get("sector_de_avg"),
                    d.get("sector_rev_growth_avg"),
                    d.get("shares_outstanding"), d.get("net_debt"),
                    d.get("total_revenue"), d.get("free_cash_flow"),
                    d.get("total_debt"), d.get("total_cash"),
                    d.get("net_income"), d.get("data_source"),
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
