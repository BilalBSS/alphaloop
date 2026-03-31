# / discounted cash flow with monte carlo simulation
# / runs N simulations with randomized growth rates, margins, terminal multiples
# / output: probability distribution of fair value (median, p10, p90, upside%)
# / stores results to dcf_valuations table

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
import json

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# / default assumptions
DEFAULT_PROJECTION_YEARS = 5
DEFAULT_TERMINAL_GROWTH = 0.025  # 2.5% perpetuity growth
DEFAULT_DISCOUNT_RATE = 0.10    # 10% wacc
DEFAULT_NUM_SIMULATIONS = 10_000

# / growth-rate-to-terminal-multiple anchor points for piecewise-linear interpolation
# / based on empirical EV/FCF multiples from sell-side equity research
TERMINAL_MULTIPLE_TIERS: list[tuple[float, float]] = [
    (-0.20, 6.0),   # severe decline: distressed
    (-0.10, 8.0),   # moderate decline
    (0.00, 10.0),   # zero growth: mature, bond-like
    (0.05, 12.0),   # low growth: utilities, staples
    (0.10, 15.0),   # moderate growth: established tech
    (0.15, 18.0),   # solid growth: mid-cap tech
    (0.20, 21.0),   # high growth: growth SaaS
    (0.30, 27.0),   # very high growth: scaling platforms
    (0.50, 33.0),   # hyper growth: category creators
    (0.75, 38.0),   # extreme growth: early dominance
    (1.00, 40.0),   # cap: no multiple above 40x
]

# / fcf margin adjustment: premium for high-margin, discount for low-margin
FCF_MARGIN_BASELINE = 0.15
FCF_MARGIN_WEIGHT = 0.5
FCF_MARGIN_FLOOR = 0.70
FCF_MARGIN_CAP = 1.30

# / uncertainty scales with base multiple
TERMINAL_MULTIPLE_CV = 0.18


def compute_terminal_multiple(
    revenue_growth: float,
    fcf_margin: float | None = None,
) -> float:
    # / piecewise-linear interpolation of growth rate to terminal multiple
    # / with optional fcf margin adjustment
    if revenue_growth is None or np.isnan(revenue_growth):
        return 15.0  # safe default

    growth_rates = [t[0] for t in TERMINAL_MULTIPLE_TIERS]
    multiples = [t[1] for t in TERMINAL_MULTIPLE_TIERS]
    base = float(np.interp(revenue_growth, growth_rates, multiples))

    if fcf_margin is not None and not np.isnan(fcf_margin):
        # / reduce margin premium for high-growth stocks (growth already in base multiple)
        effective_weight = FCF_MARGIN_WEIGHT
        if revenue_growth > 0.30:
            effective_weight *= 0.5  # halve margin premium for hyper-growth
        elif revenue_growth > 0.15:
            effective_weight *= 0.75
        adjustment = 1.0 + effective_weight * (fcf_margin - FCF_MARGIN_BASELINE)
        adjustment = max(FCF_MARGIN_FLOOR, min(FCF_MARGIN_CAP, adjustment))
        base *= adjustment

    # / cap after margin adjustment so premium doesn't exceed 40x
    base = min(base, 40.0)

    return round(base, 1)


def compute_terminal_multiple_std(base_multiple: float) -> float:
    # / uncertainty proportional to base multiple
    return round(base_multiple * TERMINAL_MULTIPLE_CV, 1)


@dataclass
class DCFAssumptions:
    revenue: float                  # current annual revenue
    fcf_margin: float               # current fcf margin (0.0-1.0)
    revenue_growth: float           # base case annual growth rate
    growth_std: float = 0.05        # std dev for growth rate randomization
    margin_std: float = 0.03        # std dev for margin randomization
    terminal_multiple: float = 15.0 # base ev/fcf terminal multiple
    terminal_multiple_std: float = 3.0
    discount_rate: float = DEFAULT_DISCOUNT_RATE
    projection_years: int = DEFAULT_PROJECTION_YEARS
    terminal_growth: float = DEFAULT_TERMINAL_GROWTH
    shares_outstanding: float = 1.0 # for per-share value
    net_debt: float = 0.0           # debt - cash, subtracted from ev


@dataclass
class DCFResult:
    symbol: str
    date: date
    fair_value_median: float
    fair_value_p10: float           # 10th percentile (bearish)
    fair_value_p90: float           # 90th percentile (bullish)
    current_price: float
    upside_pct: float               # median vs current price
    num_simulations: int
    confidence: str                 # high, medium, low
    assumptions: dict[str, Any] = field(default_factory=dict)


def run_dcf_simulation(
    assumptions: DCFAssumptions,
    num_simulations: int = DEFAULT_NUM_SIMULATIONS,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    # / run monte carlo dcf, returns array of per-share fair values
    rng = rng or np.random.default_rng()

    if assumptions.revenue <= 0 or assumptions.shares_outstanding <= 0:
        logger.warning("dcf_invalid_inputs", revenue=assumptions.revenue)
        return np.array([0.0])

    n = num_simulations
    years = assumptions.projection_years

    # / antithetic variates: generate n/2 Z-samples, mirror with -Z for n total
    # / negatively-correlated pairs reduce variance on monotone payoffs
    half = (n + 1) // 2

    z_growth = rng.standard_normal((half, years))
    z_margins = rng.standard_normal((half, years))
    z_terminal = rng.standard_normal(half)

    growth_rates = np.vstack([
        assumptions.revenue_growth + assumptions.growth_std * z_growth,
        assumptions.revenue_growth + assumptions.growth_std * (-z_growth),
    ])[:n]
    growth_rates = np.clip(growth_rates, -0.50, 1.0)

    # / mean-revert growth rates toward long-term average over projection period
    long_term_growth = assumptions.terminal_growth
    # / higher growth = faster decay (NVDA at 65% should decay faster than AAPL at 8%)
    # / revenue size also accelerates reversion — law of large numbers
    base_reversion = min(0.35, 0.12 + abs(assumptions.revenue_growth) * 0.5)
    if assumptions.revenue > 200_000_000_000:  # >$200B: extreme reversion
        size_reversion_boost = 0.15
    elif assumptions.revenue > 100_000_000_000:  # >$100B
        size_reversion_boost = 0.08
    elif assumptions.revenue > 50_000_000_000:  # >$50B
        size_reversion_boost = 0.04
    else:
        size_reversion_boost = 0.0
    reversion_speed = min(0.55, base_reversion + size_reversion_boost)
    for year in range(1, years):
        growth_rates[:, year] = (
            (1 - reversion_speed) * growth_rates[:, year - 1]
            + reversion_speed * long_term_growth
            + assumptions.growth_std * 0.3 * rng.standard_normal(n)
        )
    growth_rates = np.clip(growth_rates, -0.50, 1.0)

    # / dampen growth for large-revenue companies (law of large numbers)
    # / stronger damping tiers: mega-caps face much harder growth scaling
    if assumptions.revenue > 200_000_000_000:  # >$200B: mega-cap
        size_factor = min(1.0, 200_000_000_000 / assumptions.revenue)
        growth_rates *= size_factor ** 0.5
        # / additional absolute cap: no mega-cap sustains >30% yoy
        growth_rates = np.clip(growth_rates, -0.50, 0.30)
    elif assumptions.revenue > 100_000_000_000:  # >$100B
        size_factor = min(1.0, 100_000_000_000 / assumptions.revenue)
        growth_rates *= size_factor ** 0.4
        growth_rates = np.clip(growth_rates, -0.50, 0.45)
    elif assumptions.revenue > 50_000_000_000:  # >$50B
        size_factor = min(1.0, 50_000_000_000 / assumptions.revenue)
        growth_rates *= size_factor ** 0.2

    margins = np.vstack([
        assumptions.fcf_margin + assumptions.margin_std * z_margins,
        assumptions.fcf_margin + assumptions.margin_std * (-z_margins),
    ])[:n]
    margins = np.clip(margins, -0.5, 0.8)

    terminal_multiples = np.concatenate([
        assumptions.terminal_multiple + assumptions.terminal_multiple_std * z_terminal,
        assumptions.terminal_multiple + assumptions.terminal_multiple_std * (-z_terminal),
    ])[:n]
    terminal_multiples = np.clip(terminal_multiples, 3.0, 50.0)

    # / vectorized dcf: cumprod for compounding, broadcast for discounting
    discount_factors = np.array([
        1.0 / (1.0 + assumptions.discount_rate) ** (y + 1)
        for y in range(years)
    ])

    # / cumulative revenue growth per simulation per year
    cum_growth = np.cumprod(1.0 + growth_rates, axis=1)
    revenues = assumptions.revenue * cum_growth

    # / present value of projected fcf
    fcfs = revenues * margins
    pv_fcfs = (fcfs * discount_factors[np.newaxis, :]).sum(axis=1)

    # / terminal value using last year's revenue and margin
    terminal_fcfs = revenues[:, -1] * margins[:, -1]
    terminal_values = terminal_fcfs * terminal_multiples
    pv_terminals = terminal_values * discount_factors[-1]

    enterprise_values = pv_fcfs + pv_terminals

    # / equity value = ev - net debt
    equity_values = enterprise_values - assumptions.net_debt
    equity_values = np.maximum(equity_values, 0.0)

    # / per-share value
    per_share = equity_values / assumptions.shares_outstanding

    return per_share


def compute_dcf(
    symbol: str,
    current_price: float,
    assumptions: DCFAssumptions,
    as_of: date | None = None,
    num_simulations: int = DEFAULT_NUM_SIMULATIONS,
    rng: np.random.Generator | None = None,
) -> DCFResult:
    # / run simulation and package results
    as_of = as_of or date.today()

    fair_values = run_dcf_simulation(assumptions, num_simulations, rng)

    # / mega-cap sanity cap: market is rarely that wrong on liquid large-caps
    # / only applies to companies with real revenue (>$10B)
    if current_price > 0 and assumptions.revenue > 10_000_000_000:
        if assumptions.revenue > 200_000_000_000:
            max_fv = current_price * 2.5
        elif assumptions.revenue > 100_000_000_000:
            max_fv = current_price * 3.0
        elif assumptions.revenue > 50_000_000_000:
            max_fv = current_price * 4.0
        else:
            max_fv = current_price * 5.0
        fair_values = np.minimum(fair_values, max_fv)

    median = float(np.median(fair_values))
    p10 = float(np.percentile(fair_values, 10))
    p90 = float(np.percentile(fair_values, 90))

    upside = (median - current_price) / current_price if current_price > 0 else 0.0

    # / confidence based on spread of distribution
    spread = (p90 - p10) / median if median > 0 else float("inf")
    if spread < 0.5:
        confidence = "high"
    elif spread < 1.0:
        confidence = "medium"
    else:
        confidence = "low"

    # / sanity: extreme divergence from market price -> low confidence
    if current_price > 0:
        price_ratio = median / current_price
        if price_ratio > 5.0 or price_ratio < 0.1:
            confidence = "low"

    return DCFResult(
        symbol=symbol,
        date=as_of,
        fair_value_median=round(median, 2),
        fair_value_p10=round(p10, 2),
        fair_value_p90=round(p90, 2),
        current_price=round(current_price, 2),
        upside_pct=round(upside, 4),
        num_simulations=num_simulations,
        confidence=confidence,
        assumptions={
            "revenue": assumptions.revenue,
            "fcf_margin": assumptions.fcf_margin,
            "revenue_growth": assumptions.revenue_growth,
            "growth_std": assumptions.growth_std,
            "margin_std": assumptions.margin_std,
            "terminal_multiple": assumptions.terminal_multiple,
            "discount_rate": assumptions.discount_rate,
            "projection_years": assumptions.projection_years,
            "net_debt": assumptions.net_debt,
            "shares_outstanding": assumptions.shares_outstanding,
        },
    )


async def build_assumptions_from_db(pool, symbol: str) -> DCFAssumptions | None:
    # / pull fundamentals + latest price to construct dcf assumptions
    async with pool.acquire() as conn:
        fund_row = await conn.fetchrow(
            """
            SELECT * FROM fundamentals
            WHERE symbol = $1 ORDER BY date DESC LIMIT 1
            """,
            symbol,
        )

        price_row = await conn.fetchrow(
            """
            SELECT close FROM market_data
            WHERE symbol = $1 ORDER BY date DESC LIMIT 1
            """,
            symbol,
        )

    if not fund_row:
        logger.warning("no_fundamentals_for_dcf", symbol=symbol)
        return None

    fcf_margin = float(fund_row["fcf_margin"]) if fund_row["fcf_margin"] else 0.10
    revenue_growth = float(fund_row["revenue_growth_1y"]) if fund_row["revenue_growth_1y"] else 0.05
    price = float(price_row["close"]) if price_row else 0.0

    # / use real shares_outstanding and total_revenue from edgar/finnhub when available
    shares = float(fund_row["shares_outstanding"]) if fund_row.get("shares_outstanding") else None
    total_rev = float(fund_row["total_revenue"]) if fund_row.get("total_revenue") else None
    net_debt_val = float(fund_row["net_debt"]) if fund_row.get("net_debt") else 0.0

    if total_rev and shares and shares > 0:
        # / real revenue and shares available — compute total DCF
        revenue = total_rev
        shares_out = shares
    else:
        # / fallback: per-share basis using P/S ratio
        ps = float(fund_row["ps_ratio"]) if fund_row["ps_ratio"] else None
        if ps and ps > 0 and price > 0:
            revenue = price / ps  # revenue per share
        else:
            revenue = price * 0.3 if price > 0 else 100.0
        shares_out = 1.0
        net_debt_val = 0.0  # / can't use total net debt with per-share revenue

    # / margin expansion: high-growth companies with thin margins will likely expand
    target_margin = 0.15
    if fcf_margin < target_margin and revenue_growth > 0.08:
        margin_expansion = (target_margin - fcf_margin) * 0.5
        fcf_margin = fcf_margin + margin_expansion
        margin_std = 0.05
    else:
        margin_std = 0.03

    tm = compute_terminal_multiple(revenue_growth, fcf_margin)

    # / quality floor: high-FCF, net-cash companies deserve premium multiples
    # / mega-cap cash cows (AAPL, MSFT) trade at 30-40x EV/FCF due to
    # / buybacks, recurring revenue, ecosystem moat — model must reflect this
    is_mega_cap = revenue > 100_000_000_000
    if fcf_margin > 0.25 and net_debt_val < 0:
        if is_mega_cap:
            # / scale floor with margin: 25% -> 34x, 35% -> 37x, 50%+ -> 40x
            margin_bonus = min(6.0, (fcf_margin - 0.25) * 24.0)
            tm = max(tm, 34.0 + margin_bonus)
        else:
            tm = max(tm, 25.0)
    elif fcf_margin > 0.20 and net_debt_val < 0:
        if is_mega_cap:
            tm = max(tm, 27.0)
        else:
            tm = max(tm, 21.0)

    # / buyback + capital return premium
    if net_debt_val < 0 and revenue > 0 and fcf_margin > 0.20:
        cash_to_rev = abs(net_debt_val) / revenue
        if cash_to_rev > 0.2:
            tm *= 1.08  # significant cash hoard + returns
        elif cash_to_rev > 0.08:
            tm *= 1.05  # moderate capital return capacity

    tm_std = compute_terminal_multiple_std(tm)
    # / projection years: shorter for mega-caps (growth can't compound as long)
    if revenue > 200_000_000_000:
        projection_years = 5  # mega-cap: 5yr max regardless of growth
    elif revenue > 100_000_000_000:
        projection_years = 6 if revenue_growth > 0.15 else 5
    else:
        projection_years = 7 if revenue_growth > 0.15 else 5

    return DCFAssumptions(
        revenue=revenue,
        fcf_margin=fcf_margin,
        revenue_growth=revenue_growth,
        margin_std=margin_std,
        terminal_multiple=tm,
        terminal_multiple_std=tm_std,
        projection_years=projection_years,
        net_debt=net_debt_val,
        shares_outstanding=shares_out,
    )


async def analyze_dcf(
    pool,
    symbol: str,
    current_price: float | None = None,
    as_of: date | None = None,
    num_simulations: int = DEFAULT_NUM_SIMULATIONS,
) -> DCFResult | None:
    # / full dcf pipeline: build assumptions from db -> run simulation -> return result
    assumptions = await build_assumptions_from_db(pool, symbol)
    if not assumptions:
        return None

    if current_price is None:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT close FROM market_data WHERE symbol = $1 ORDER BY date DESC LIMIT 1",
                symbol,
            )
        current_price = float(row["close"]) if row else 0.0

    result = compute_dcf(symbol, current_price, assumptions, as_of, num_simulations)
    logger.info(
        "dcf_analysis_complete",
        symbol=symbol,
        median=result.fair_value_median,
        upside=result.upside_pct,
        confidence=result.confidence,
    )
    return result


async def store_dcf_result(pool, result: DCFResult, regime: str | None = None) -> bool:
    # / persist dcf result to dcf_valuations table
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO dcf_valuations (
                    symbol, date, fair_value_median, fair_value_p10, fair_value_p90,
                    current_price, upside_pct, num_simulations, dcf_confidence,
                    assumptions, regime
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                ON CONFLICT (symbol, date) DO UPDATE SET
                    fair_value_median = EXCLUDED.fair_value_median,
                    fair_value_p10 = EXCLUDED.fair_value_p10,
                    fair_value_p90 = EXCLUDED.fair_value_p90,
                    current_price = EXCLUDED.current_price,
                    upside_pct = EXCLUDED.upside_pct,
                    num_simulations = EXCLUDED.num_simulations,
                    dcf_confidence = EXCLUDED.dcf_confidence,
                    assumptions = EXCLUDED.assumptions,
                    regime = EXCLUDED.regime
                """,
                result.symbol, result.date,
                Decimal(str(result.fair_value_median)),
                Decimal(str(result.fair_value_p10)),
                Decimal(str(result.fair_value_p90)),
                Decimal(str(result.current_price)),
                Decimal(str(result.upside_pct)),
                result.num_simulations,
                result.confidence,
                json.dumps(result.assumptions),
                regime,
            )
        return True
    except Exception as exc:
        logger.warning("dcf_store_failed", symbol=result.symbol, error=str(exc))
        return False
