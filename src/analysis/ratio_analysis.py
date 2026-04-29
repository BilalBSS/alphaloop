
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
    pe_score: float | None = None  # / 0-100, higher = more
    ps_score: float | None = None
    peg_score: float | None = None
    fcf_margin_score: float | None = None
    debt_equity_score: float | None = None
    composite_score: float | None = None  # / weighted average of available
    details: dict[str, Any] = field(default_factory=dict)


WEIGHTS = {
    "pe": 0.25,
    "ps": 0.15,
    "peg": 0.25,
    "fcf_margin": 0.20,
    "debt_equity": 0.15,
}


def _ratio_score(
    value: Decimal | None, sector_avg: Decimal | None, abs_max: float, abs_min: float,
) -> float | None:
    if value is None:
        return None
    val_f = float(value)
    if val_f < 0:
        return 0.0
    if sector_avg is not None and float(sector_avg) > 0:
        ratio = val_f / float(sector_avg)
        score = max(0.0, min(100.0, (2.0 - ratio) / 1.5 * 100))
    else:
        score = max(0.0, min(100.0, (abs_max - val_f) / (abs_max - abs_min) * 100))
    return round(score, 1)


def score_pe(pe: Decimal | None, sector_avg: Decimal | None) -> float | None:
    return _ratio_score(pe, sector_avg, 50.0, 10.0)


def score_ps(ps: Decimal | None, sector_avg: Decimal | None) -> float | None:
    return _ratio_score(ps, sector_avg, 15.0, 2.0)


def score_peg(peg: Decimal | None) -> float | None:
    if peg is None:
        return None
    peg_f = float(peg)
    if peg_f <= 0:
        return 0.0  # / negative peg = negative

    score = max(0.0, min(100.0, (3.0 - peg_f) / 2.5 * 100))
    return round(score, 1)


def score_fcf_margin(fcf: Decimal | None) -> float | None:
    if fcf is None:
        return None
    fcf_f = float(fcf)

    score = max(0.0, min(100.0, (fcf_f + 0.10) / 0.40 * 100))
    return round(score, 1)


def score_debt_equity(de: Decimal | None) -> float | None:
    # / lower debt/equity = better
    if de is None:
        return None
    de_f = float(de)

    score = max(0.0, min(100.0, (3.0 - de_f) / 3.0 * 100))
    return round(score, 1)


def compute_ratio_score(fundamentals: dict[str, Any]) -> RatioScore:
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
            "revenue_growth_1y": str(fundamentals.get("revenue_growth_1y")) if fundamentals.get("revenue_growth_1y") else None,
            "sector_pe_avg": str(fundamentals.get("sector_pe_avg")) if fundamentals.get("sector_pe_avg") else None,
            "sector_ps_avg": str(fundamentals.get("sector_ps_avg")) if fundamentals.get("sector_ps_avg") else None,
            "weights_used": total_weight,
        },
    )


async def analyze_ratios(pool, symbol: str, as_of: date | None = None) -> RatioScore | None:
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
    results = []
    for symbol in symbols:
        try:
            score = await analyze_ratios(pool, symbol, as_of)
            if score:
                results.append(score)
        except Exception as exc:
            logger.warning("ratio_analysis_failed", symbol=symbol, error=str(exc))
    return results
