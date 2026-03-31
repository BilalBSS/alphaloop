# / data validation — catches wrong data, not just missing data
# / run after ingestion, before storage

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ValidationResult:
    valid: bool
    field: str
    value: Any
    reason: str = ""


# / bounds: (min, max) inclusive. none = no bound.
PRICE_BOUNDS = (Decimal("0.0001"), Decimal("999999"))
VOLUME_BOUNDS = (0, 1_000_000_000_000)
PE_BOUNDS = (Decimal("-1000"), Decimal("10000"))
PS_BOUNDS = (Decimal("0"), Decimal("10000"))
PEG_BOUNDS = (Decimal("-100"), Decimal("1000"))
GROWTH_BOUNDS = (Decimal("-1.0"), Decimal("100.0"))
FCF_MARGIN_BOUNDS = (Decimal("-50.0"), Decimal("5.0"))
DEBT_EQUITY_BOUNDS = (Decimal("0"), Decimal("1000"))
SENTIMENT_BOUNDS = (Decimal("-1.0"), Decimal("1.0"))


def _check_bound(
    field: str,
    value: Any,
    min_val: Any | None = None,
    max_val: Any | None = None,
) -> ValidationResult:
    # / check if value is within bounds
    if value is None:
        return ValidationResult(valid=True, field=field, value=value)

    try:
        numeric = Decimal(str(value))
    except Exception:
        return ValidationResult(
            valid=False, field=field, value=value,
            reason=f"not numeric: {value!r}",
        )

    if min_val is not None and numeric < Decimal(str(min_val)):
        return ValidationResult(
            valid=False, field=field, value=value,
            reason=f"{field}={value} below min {min_val}",
        )
    if max_val is not None and numeric > Decimal(str(max_val)):
        return ValidationResult(
            valid=False, field=field, value=value,
            reason=f"{field}={value} above max {max_val}",
        )
    return ValidationResult(valid=True, field=field, value=value)


def validate_market_data(row: dict) -> list[ValidationResult]:
    # / validate ohlcv row
    results = []
    for field in ("open", "high", "low", "close"):
        results.append(_check_bound(field, row.get(field), *PRICE_BOUNDS))
    results.append(_check_bound("volume", row.get("volume"), *VOLUME_BOUNDS))

    high = row.get("high")
    low = row.get("low")
    open_val = row.get("open")
    close = row.get("close")

    if high is not None and low is not None:
        h, l = Decimal(str(high)), Decimal(str(low))
        if h < l:
            results.append(ValidationResult(
                valid=False, field="high_low",
                value=f"high={high}, low={low}",
                reason="high < low",
            ))
        # / open and close must be within high-low range
        for fname, fval in [("open", open_val), ("close", close)]:
            if fval is not None:
                v = Decimal(str(fval))
                if v > h or v < l:
                    results.append(ValidationResult(
                        valid=False, field=f"{fname}_range",
                        value=f"{fname}={fval}, high={high}, low={low}",
                        reason=f"{fname} outside high-low range",
                    ))

    return results


def validate_fundamentals(row: dict) -> list[ValidationResult]:
    # / validate fundamental data row
    return [
        _check_bound("pe_ratio", row.get("pe_ratio"), *PE_BOUNDS),
        _check_bound("pe_forward", row.get("pe_forward"), *PE_BOUNDS),
        _check_bound("ps_ratio", row.get("ps_ratio"), *PS_BOUNDS),
        _check_bound("peg_ratio", row.get("peg_ratio"), *PEG_BOUNDS),
        _check_bound("revenue_growth_1y", row.get("revenue_growth_1y"), *GROWTH_BOUNDS),
        _check_bound("revenue_growth_3y", row.get("revenue_growth_3y"), *GROWTH_BOUNDS),
        _check_bound("fcf_margin", row.get("fcf_margin"), *FCF_MARGIN_BOUNDS),
        _check_bound("debt_to_equity", row.get("debt_to_equity"), *DEBT_EQUITY_BOUNDS),
    ]


def validate_sentiment(row: dict) -> list[ValidationResult]:
    # / validate news sentiment row
    return [
        _check_bound("sentiment_score", row.get("sentiment_score"), *SENTIMENT_BOUNDS),
    ]


# / alias for market_data.py import compatibility
validate_ohlcv = validate_market_data


