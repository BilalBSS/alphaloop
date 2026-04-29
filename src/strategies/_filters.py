from __future__ import annotations

from collections.abc import Callable

FILTER_HANDLERS: dict[str, Callable] = {}


def register_filter(config_key: str):
    def decorator(fn):
        FILTER_HANDLERS[config_key] = fn
        return fn
    return decorator


def _strict(filters: dict) -> bool:
    return filters.get("strict_data", True)


def _threshold_filter(field: str, op: str, label: str, *, pct: bool = False):
    fmt = "{:.2%}" if pct else "{:.2f}"
    word = "min" if op == "min" else "max"
    sym = "<" if op == "min" else ">"

    def _f(analysis, threshold, filters) -> tuple[bool, str]:
        value = getattr(analysis, field, None)
        if value is None:
            if _strict(filters):
                return False, f"{field} data unavailable"
            return True, ""
        if (op == "min" and value < threshold) or (op == "max" and value > threshold):
            tfmt = fmt.format(threshold) if pct else f"{threshold}"
            return False, f"{label} {fmt.format(value)} {sym} {word} {tfmt}"
        return True, ""

    return _f


@register_filter("pe_ratio_max")
def _filter_pe_max(analysis, threshold, filters) -> tuple[bool, str]:
    if analysis.pe_ratio is None:
        if _strict(filters):
            return False, "pe_ratio data unavailable"
        return True, ""
    if analysis.pe_ratio > threshold:
        return False, f"pe {analysis.pe_ratio:.1f} > max {threshold}"
    return True, ""


@register_filter("pe_vs_sector")
def _filter_pe_vs_sector(analysis, value, filters) -> tuple[bool, str]:
    if value != "below_average":
        return True, ""
    if analysis.pe_ratio is None or analysis.sector_pe_avg is None:
        if _strict(filters):
            return False, "pe or sector_pe data unavailable"
        return True, ""
    if analysis.pe_ratio > analysis.sector_pe_avg:
        return False, f"pe {analysis.pe_ratio:.1f} above sector avg {analysis.sector_pe_avg:.1f}"
    return True, ""


register_filter("revenue_growth_min")(
    _threshold_filter("revenue_growth", "min", "revenue growth", pct=True))
register_filter("fcf_margin_min")(
    _threshold_filter("fcf_margin", "min", "fcf margin", pct=True))
register_filter("debt_to_equity_max")(
    _threshold_filter("debt_to_equity", "max", "d/e"))
register_filter("dcf_upside_min")(
    _threshold_filter("dcf_upside", "min", "dcf upside", pct=True))


@register_filter("insider_buying_recent")
def _filter_insider_buying_recent(analysis, required, filters) -> tuple[bool, str]:
    if required is not True:
        return True, ""
    if analysis.insider_net_buy_ratio is None:
        if _strict(filters):
            return False, "insider_activity data unavailable"
        return True, ""
    if analysis.insider_net_buy_ratio <= 0:
        return False, f"no recent insider buying (ratio={analysis.insider_net_buy_ratio:.2f})"
    return True, ""


@register_filter("nvt_max")
def _filter_nvt_max(analysis, threshold, filters) -> tuple[bool, str]:
    # / crypto: nvt is none-tolerant
    if analysis.nvt_ratio is None:
        return True, ""
    if analysis.nvt_ratio > threshold:
        return False, f"nvt {analysis.nvt_ratio:.1f} > max {threshold}"
    return True, ""


@register_filter("funding_rate_max")
def _filter_funding_rate_max(analysis, threshold, filters) -> tuple[bool, str]:
    if analysis.funding_rate is None:
        return True, ""
    if abs(analysis.funding_rate) > threshold:
        return False, f"funding rate {analysis.funding_rate:.4f} exceeds max {threshold}"
    return True, ""


@register_filter("news_sentiment_min")
def _filter_news_sentiment_min(analysis, threshold, filters) -> tuple[bool, str]:
    if analysis.news_sentiment_score is None:
        return True, ""
    if analysis.news_sentiment_score < threshold:
        return False, f"sentiment {analysis.news_sentiment_score:.2f} < min {threshold}"
    return True, ""


register_filter("macro_score_min")(
    _threshold_filter("macro_score", "min", "macro score"))
register_filter("congressional_buy_ratio_min")(
    _threshold_filter("congressional_buy_ratio", "min", "congressional buy ratio"))
register_filter("analyst_consensus_min")(
    _threshold_filter("analyst_consensus", "min", "analyst consensus"))
register_filter("price_target_upside_min")(
    _threshold_filter("price_target_upside", "min", "price target upside", pct=True))
register_filter("earnings_revision_momentum_min")(
    _threshold_filter("earnings_revision_momentum", "min", "earnings revision momentum"))
register_filter("short_pct_float_max")(
    _threshold_filter("short_pct_float", "max", "short pct float", pct=True))
register_filter("dark_pool_ratio_max")(
    _threshold_filter("dark_pool_ratio", "max", "dark pool ratio"))
register_filter("iv_rank_min")(
    _threshold_filter("iv_rank", "min", "iv rank"))
register_filter("iv_rank_max")(
    _threshold_filter("iv_rank", "max", "iv rank"))
register_filter("put_call_ratio_max")(
    _threshold_filter("put_call_ratio", "max", "put/call ratio"))
