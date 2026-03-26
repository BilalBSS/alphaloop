# / fundamental ratio analysis: score stocks 0-100 based on valuation ratios
# / reads from fundamentals table, compares vs sector averages
# / graceful: returns partial scores when some ratios missing

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class RatioScore:
    symbol: str
    date: date
    pe_score: float | None = None       # 0-100, higher = more undervalued
    ps_score: float | None = None
    peg_score: float | None = None
    fcf_margin_score: float | None = None
    debt_equity_score: float | None = None
    composite_score: float | None = None  # weighted average of available scores
    details: dict[str, Any] = field(default_factory=dict)


# / score weights — pe and peg matter most for value assessment
WEIGHTS = {
    "pe": 0.25,
    "ps": 0.15,
    "peg": 0.25,
    "fcf_margin": 0.20,
    "debt_equity": 0.15,
}


def score_pe(pe: Decimal | None, sector_avg: Decimal | None) -> float | None:
    # / lower pe vs sector = better score
    # / pe < 0 means negative earnings = score 0
    if pe is None:
        return None
    pe_f = float(pe)
    if pe_f < 0:
        return 0.0

    if sector_avg is not None and float(sector_avg) > 0:
        ratio = pe_f / float(sector_avg)
        # / ratio < 0.5 = 100, ratio > 2.0 = 0, linear between
        score = max(0.0, min(100.0, (2.0 - ratio) / 1.5 * 100))
    else:
        # / no sector avg — use absolute scale
        # / pe < 10 = 100, pe > 50 = 0
        score = max(0.0, min(100.0, (50.0 - pe_f) / 40.0 * 100))

    return round(score, 1)


def score_ps(ps: Decimal | None, sector_avg: Decimal | None) -> float | None:
    # / lower ps vs sector = better
    if ps is None:
        return None
    ps_f = float(ps)
    if ps_f < 0:
        return 0.0

    if sector_avg is not None and float(sector_avg) > 0:
        ratio = ps_f / float(sector_avg)
        score = max(0.0, min(100.0, (2.0 - ratio) / 1.5 * 100))
    else:
        # / absolute: ps < 2 = 100, ps > 15 = 0
        score = max(0.0, min(100.0, (15.0 - ps_f) / 13.0 * 100))

    return round(score, 1)


def score_peg(peg: Decimal | None) -> float | None:
    # / peg < 1 = undervalued, 1-2 = fair, > 2 = overvalued
    if peg is None:
        return None
    peg_f = float(peg)
    if peg_f <= 0:
        return 0.0  # negative peg = negative growth = bad

    # / peg 0.5 = 100, peg 3.0 = 0
    score = max(0.0, min(100.0, (3.0 - peg_f) / 2.5 * 100))
    return round(score, 1)


def score_fcf_margin(fcf: Decimal | None) -> float | None:
    # / higher fcf margin = better
    if fcf is None:
        return None
    fcf_f = float(fcf)

    # / fcf_margin >= 0.30 = 100, <= -0.10 = 0
    score = max(0.0, min(100.0, (fcf_f + 0.10) / 0.40 * 100))
    return round(score, 1)


def score_debt_equity(de: Decimal | None) -> float | None:
    # / lower debt/equity = better
    if de is None:
        return None
    de_f = float(de)

    # / yfinance returns d/e as percentage (50 = 0.5x)
    # / normalize: divide by 100 if > 10 (likely percentage format)
    if de_f > 10:
        de_f = de_f / 100.0

    # / d/e 0 = 100, d/e >= 3.0 = 0
    score = max(0.0, min(100.0, (3.0 - de_f) / 3.0 * 100))
    return round(score, 1)


def compute_ratio_score(fundamentals: dict[str, Any]) -> RatioScore:
    # / compute all ratio scores from a fundamentals row
    symbol = fundamentals.get("symbol", "UNKNOWN")
    as_of = fundamentals.get("date", date.today())

    pe = score_pe(fundamentals.get("pe_ratio"), fundamentals.get("sector_pe_avg"))
    ps = score_ps(fundamentals.get("ps_ratio"), fundamentals.get("sector_ps_avg"))
    peg = score_peg(fundamentals.get("peg_ratio"))
    fcf = score_fcf_margin(fundamentals.get("fcf_margin"))
    de = score_debt_equity(fundamentals.get("debt_to_equity"))

    scores = {
        "pe": pe,
        "ps": ps,
        "peg": peg,
        "fcf_margin": fcf,
        "debt_equity": de,
    }

    # / weighted composite from available scores
    total_weight = 0.0
    weighted_sum = 0.0
    for key, val in scores.items():
        if val is not None:
            w = WEIGHTS[key]
            weighted_sum += val * w
            total_weight += w

    composite = round(weighted_sum / total_weight, 1) if total_weight > 0 else None

    return RatioScore(
        symbol=symbol,
        date=as_of,
        pe_score=pe,
        ps_score=ps,
        peg_score=peg,
        fcf_margin_score=fcf,
        debt_equity_score=de,
        composite_score=composite,
        details={
            "pe_ratio": str(fundamentals.get("pe_ratio")) if fundamentals.get("pe_ratio") else None,
            "ps_ratio": str(fundamentals.get("ps_ratio")) if fundamentals.get("ps_ratio") else None,
            "peg_ratio": str(fundamentals.get("peg_ratio")) if fundamentals.get("peg_ratio") else None,
            "fcf_margin": str(fundamentals.get("fcf_margin")) if fundamentals.get("fcf_margin") else None,
            "debt_to_equity": str(fundamentals.get("debt_to_equity")) if fundamentals.get("debt_to_equity") else None,
            "weights_used": total_weight,
        },
    )


async def analyze_ratios(pool, symbol: str, as_of: date | None = None) -> RatioScore | None:
    # / fetch latest fundamentals from db and score them
    as_of = as_of or date.today()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM fundamentals
            WHERE symbol = $1 AND date <= $2
            ORDER BY date DESC LIMIT 1
            """,
            symbol, as_of,
        )

    if not row:
        logger.warning("no_fundamentals_for_ratio_analysis", symbol=symbol)
        return None

    fundamentals = dict(row)
    result = compute_ratio_score(fundamentals)
    logger.info("ratio_analysis_complete", symbol=symbol, composite=result.composite_score)
    return result


async def analyze_ratios_batch(
    pool,
    symbols: list[str],
    as_of: date | None = None,
) -> list[RatioScore]:
    # / score all symbols, skip failures
    results = []
    for symbol in symbols:
        try:
            score = await analyze_ratios(pool, symbol, as_of)
            if score:
                results.append(score)
        except Exception as exc:
            logger.warning("ratio_analysis_failed", symbol=symbol, error=str(exc))
    return results
