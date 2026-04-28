# / analysis-data signal handlers: indicator -> (sig, market_data, analysis_data) -> (passed, strength, reason)
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from src.strategies.base_strategy import AnalysisData

ANALYSIS_HANDLERS: dict[str, Callable[..., tuple[bool, float, str]]] = {}


def register_analysis(name: str):
    def decorator(fn):
        ANALYSIS_HANDLERS[name] = fn
        return fn
    return decorator


@register_analysis("regime")
def _eval_regime(
    sig: dict[str, Any], market_data: pd.DataFrame,
    analysis_data: AnalysisData | None,
) -> tuple[bool, float, str]:
    if analysis_data is None or not analysis_data.regime:
        return False, 0.0, "regime: no analysis data"
    regime = analysis_data.regime
    condition = sig.get("condition", "")
    if condition == "is":
        target = sig.get("value")
        passed = regime == target
        return passed, 0.5 if passed else 0.0, f"regime={regime} {'==' if passed else '!='} {target}"
    if condition == "in":
        values = sig.get("values") or []
        passed = regime in values
        return passed, 0.5 if passed else 0.0, f"regime={regime} {'in' if passed else 'not in'} {values}"
    return False, 0.0, f"unknown regime condition: {condition}"


@register_analysis("sector_relative_strength")
def _eval_sector_relative_strength(
    sig: dict[str, Any], market_data: pd.DataFrame,
    analysis_data: AnalysisData | None,
) -> tuple[bool, float, str]:
    if analysis_data is None or analysis_data.sector_relative_strength is None:
        return False, 0.0, "sector_relative_strength: no analysis data"
    rs = float(analysis_data.sector_relative_strength)
    thr = float(sig.get("threshold", 0.0))
    condition = sig.get("condition", "above")
    if condition == "above":
        passed = rs > thr
        return passed, min(1.0, rs / max(thr, 0.01)) if passed else 0.0, f"sector_rs={rs:.4f} {'>' if passed else '<='} {thr}"
    if condition == "below":
        passed = rs < thr
        return passed, 0.5 if passed else 0.0, f"sector_rs={rs:.4f} {'<' if passed else '>='} {thr}"
    return False, 0.0, f"unknown sector_rs condition: {condition}"


@register_analysis("earnings_surprise")
def _eval_earnings_surprise(
    sig: dict[str, Any], market_data: pd.DataFrame,
    analysis_data: AnalysisData | None,
) -> tuple[bool, float, str]:
    if analysis_data is None or analysis_data.earnings_surprise_pct is None:
        return False, 0.0, "earnings_surprise: no analysis data"
    surprise = float(analysis_data.earnings_surprise_pct)
    thr_raw = sig.get("threshold_pct")
    if thr_raw is None:
        thr_raw = sig.get("threshold")
    thr = float(thr_raw) if thr_raw is not None else 0.05
    max_days_raw = sig.get("max_days_since_report")
    max_days = int(max_days_raw) if max_days_raw is not None else 10
    # / days_to_earnings: negative = days since, positive = days until
    days_since = None
    if analysis_data.days_to_earnings is not None:
        dte = int(analysis_data.days_to_earnings)
        days_since = -dte if dte < 0 else None
    condition = sig.get("condition", "above")
    if condition == "above":
        passed = surprise > thr
        if passed and days_since is not None and days_since > max_days:
            return False, 0.0, f"earnings_surprise={surprise * 100:.1f}% passed but {days_since}d > max {max_days}d"
        return passed, 0.6 if passed else 0.0, f"earnings_surprise={surprise * 100:.1f}% {'>' if passed else '<='} {thr * 100:.1f}%"
    return False, 0.0, f"unknown earnings_surprise condition: {condition}"


@register_analysis("earnings_revision_momentum")
def _eval_earnings_revision(
    sig: dict[str, Any], market_data: pd.DataFrame,
    analysis_data: AnalysisData | None,
) -> tuple[bool, float, str]:
    if analysis_data is None or analysis_data.earnings_revision_momentum is None:
        return False, 0.0, "earnings_revision_momentum: no analysis data"
    mom = float(analysis_data.earnings_revision_momentum)
    thr = float(sig.get("threshold", 0.0))
    condition = sig.get("condition", "above")
    if condition == "above":
        passed = mom > thr
        return passed, 0.5 if passed else 0.0, f"earnings_rev_mom={mom:.4f} {'>' if passed else '<='} {thr}"
    return False, 0.0, f"unknown earnings_revision condition: {condition}"


@register_analysis("insider_cluster")
def _eval_insider_cluster(
    sig: dict[str, Any], market_data: pd.DataFrame,
    analysis_data: AnalysisData | None,
) -> tuple[bool, float, str]:
    # / approximate cluster from net_buy_ratio (raw count not in analysis_data)
    if analysis_data is None or analysis_data.insider_net_buy_ratio is None:
        return False, 0.0, "insider_cluster: no analysis data"
    ratio = float(analysis_data.insider_net_buy_ratio)
    # / count threshold reinterpreted: 2 -> ratio>0.5, 3 -> ratio>0.7 (linear)
    count_threshold = int(sig.get("threshold", 2))
    implied_ratio_floor = 0.5 + (count_threshold - 2) * 0.1
    condition = sig.get("condition", "count_above")
    if condition == "count_above":
        passed = ratio >= implied_ratio_floor
        return passed, min(1.0, ratio) if passed else 0.0, f"insider_net_ratio={ratio:.2f} {'>=' if passed else '<'} {implied_ratio_floor:.2f} (proxy for count>={count_threshold})"
    return False, 0.0, f"unknown insider_cluster condition: {condition}"


@register_analysis("insider_net_dollar")
def _eval_insider_net_dollar(
    sig: dict[str, Any], market_data: pd.DataFrame,
    analysis_data: AnalysisData | None,
) -> tuple[bool, float, str]:
    # / proxy via net_buy_ratio (analysis_data lacks raw dollar amounts)
    if analysis_data is None or analysis_data.insider_net_buy_ratio is None:
        return False, 0.0, "insider_net_dollar: no analysis data"
    ratio = float(analysis_data.insider_net_buy_ratio)
    threshold_usd = float(sig.get("threshold_usd", 100000))
    # / rescale: $100k -> 0.55, $500k -> 0.70
    implied_ratio_floor = 0.55 + (threshold_usd - 100000) * 0.15 / 400000
    passed = ratio >= implied_ratio_floor
    return passed, min(1.0, ratio) if passed else 0.0, f"insider_net_ratio={ratio:.2f} {'>=' if passed else '<'} {implied_ratio_floor:.2f} (proxy for ${threshold_usd:,.0f})"


@register_analysis("intermarket_correlation")
def _eval_intermarket_correlation(
    sig: dict[str, Any], market_data: pd.DataFrame,
    analysis_data: AnalysisData | None,
) -> tuple[bool, float, str]:
    # / proxy via intermarket_score (second series not loaded here)
    if analysis_data is None or analysis_data.intermarket_score is None:
        return False, 0.0, "intermarket_correlation: no analysis data"
    score = float(analysis_data.intermarket_score)
    thr_raw = sig.get("threshold")
    thr = float(thr_raw) if thr_raw is not None else 0.3
    passed = score >= thr
    return passed, min(1.0, score) if passed else 0.0, f"intermarket_score={score:.2f} {'>=' if passed else '<'} {thr}"


def _eval_macro_not_implemented(
    sig: dict[str, Any], market_data: pd.DataFrame,
    analysis_data: AnalysisData | None,
) -> tuple[bool, float, str]:
    # / macro indicators not yet plumbed to evaluator; registered to avoid "unknown indicator"
    indicator = sig.get("indicator", "?")
    return False, 0.0, f"{indicator}: macro data not threaded to evaluator (requires data plumbing)"


# / register macro stubs explicitly so they don't read as "unknown indicator"
ANALYSIS_HANDLERS["yield_curve"] = _eval_macro_not_implemented
ANALYSIS_HANDLERS["yield_curve_slope"] = _eval_macro_not_implemented
ANALYSIS_HANDLERS["vix"] = _eval_macro_not_implemented
ANALYSIS_HANDLERS["beta"] = _eval_macro_not_implemented
