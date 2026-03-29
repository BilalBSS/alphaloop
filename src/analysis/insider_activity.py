# / insider activity analysis: aggregate buy/sell from insider_trades table
# / net buy ratio, cluster detection, officer vs director weighting
# / produces a score indicating insider sentiment

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# / weighting: officer-level buys matter more than director buys
# / ordered list — checked first to last, first match wins
# / longer/more-specific patterns before shorter ones to avoid substring collisions
# / e.g. "director" contains "cto", "vice president" contains "president"
TITLE_WEIGHTS: list[tuple[str, float]] = [
    ("ceo", 3.0),
    ("chief executive", 3.0),
    ("chief financial", 2.5),
    ("cfo", 2.5),
    ("chief operating", 2.0),
    ("coo", 2.0),
    ("chief technology", 2.0),
    ("director", 1.0),      # before "cto" — "director" contains "cto"
    ("cto", 2.0),
    ("vice president", 1.5),  # before "president"
    ("vp ", 1.5),
    ("president", 2.0),
    ("officer", 1.5),
]

# / cluster detection: N+ insiders buying within M days
CLUSTER_MIN_INSIDERS = 3
CLUSTER_WINDOW_DAYS = 30


@dataclass
class InsiderSignal:
    symbol: str
    date: date
    signal: str            # bullish, bearish, neutral
    strength: float        # 0-100
    net_buy_ratio: float   # -1 to 1 (1 = all buys, -1 = all sells)
    total_buys: int
    total_sells: int
    buy_value: float
    sell_value: float
    cluster_detected: bool
    unique_buyers: int
    unique_sellers: int
    details: dict[str, Any] = field(default_factory=dict)
    top_trades: list[dict[str, Any]] = field(default_factory=list)  # / top trades by value for llm context


def _title_weight(title: str) -> float:
    # / return weight based on insider title — first match wins
    if not title:
        return 1.0
    title_lower = title.lower()
    for key, weight in TITLE_WEIGHTS:
        if key in title_lower:
            return weight
    return 1.0


def _detect_cluster(trades: list[dict[str, Any]], window_days: int = CLUSTER_WINDOW_DAYS) -> bool:
    # / detect if multiple insiders bought within a short window
    buys = [t for t in trades if t.get("transaction_type") == "buy"]
    if len(buys) < CLUSTER_MIN_INSIDERS:
        return False

    # / check for cluster: N+ unique insiders buying within window
    for i, trade in enumerate(buys):
        trade_date = trade.get("filing_date")
        if not trade_date:
            continue

        window_start = trade_date - timedelta(days=window_days)
        nearby = [
            t for t in buys
            if t.get("filing_date") and window_start <= t["filing_date"] <= trade_date
        ]
        unique_names = {t.get("insider_name") for t in nearby}
        if len(unique_names) >= CLUSTER_MIN_INSIDERS:
            return True

    return False


def compute_insider_signal(
    trades: list[dict[str, Any]],
    symbol: str = "UNKNOWN",
) -> InsiderSignal:
    # / analyze insider trades and produce a signal
    if not trades:
        return InsiderSignal(
            symbol=symbol,
            date=date.today(),
            signal="neutral",
            strength=0.0,
            net_buy_ratio=0.0,
            total_buys=0,
            total_sells=0,
            buy_value=0.0,
            sell_value=0.0,
            cluster_detected=False,
            unique_buyers=0,
            unique_sellers=0,
            details={"reason": "no_trades"},
        )

    # / separate buys and sells (exclude option exercises)
    buys = [t for t in trades if t.get("transaction_type") == "buy"]
    sells = [t for t in trades if t.get("transaction_type") == "sell"]

    # / weighted buy/sell values
    weighted_buy_value = 0.0
    weighted_sell_value = 0.0
    raw_buy_value = 0.0
    raw_sell_value = 0.0

    for t in buys:
        val = float(t.get("total_value", 0))
        weight = _title_weight(t.get("insider_title", ""))
        weighted_buy_value += val * weight
        raw_buy_value += val

    for t in sells:
        val = float(t.get("total_value", 0))
        weight = _title_weight(t.get("insider_title", ""))
        weighted_sell_value += val * weight
        raw_sell_value += val

    # / net buy ratio: weighted buys vs sells (-1 to 1)
    total_weighted = weighted_buy_value + weighted_sell_value
    if total_weighted > 0:
        net_buy_ratio = (weighted_buy_value - weighted_sell_value) / total_weighted
    else:
        net_buy_ratio = 0.0

    # / unique insiders
    unique_buyers = len({t.get("insider_name") for t in buys if t.get("insider_name")})
    unique_sellers = len({t.get("insider_name") for t in sells if t.get("insider_name")})

    # / cluster detection
    cluster = _detect_cluster(trades)

    # / compute signal and strength
    strength = 0.0

    # / net ratio contribution (0-50 points)
    ratio_points = abs(net_buy_ratio) * 50
    strength += ratio_points

    # / cluster bonus (0-25 points)
    if cluster:
        strength += 25.0

    # / volume of activity (0-25 points) — more trades = stronger signal
    trade_count = len(buys) + len(sells)
    activity_points = min(25.0, trade_count * 3.0)
    strength += activity_points

    strength = min(100.0, round(strength, 1))

    # / determine direction
    if net_buy_ratio > 0.2:
        signal = "bullish"
    elif net_buy_ratio < -0.2:
        signal = "bearish"
    else:
        signal = "neutral"

    # / top trades by value for llm prompt context
    all_trades = buys + sells
    sorted_trades = sorted(all_trades, key=lambda t: abs(float(t.get("total_value", 0))), reverse=True)
    top = []
    for t in sorted_trades[:5]:
        top.append({
            "name": t.get("insider_name", "Unknown"),
            "title": t.get("insider_title", ""),
            "type": t.get("transaction_type", ""),
            "shares": int(float(t.get("shares", 0))),
            "value": round(float(t.get("total_value", 0)), 2),
            "date": str(t.get("filing_date", "")),
        })

    return InsiderSignal(
        symbol=symbol,
        date=date.today(),
        signal=signal,
        strength=strength,
        net_buy_ratio=round(net_buy_ratio, 4),
        total_buys=len(buys),
        total_sells=len(sells),
        buy_value=round(raw_buy_value, 2),
        sell_value=round(raw_sell_value, 2),
        cluster_detected=cluster,
        unique_buyers=unique_buyers,
        unique_sellers=unique_sellers,
        details={
            "weighted_buy_value": round(weighted_buy_value, 2),
            "weighted_sell_value": round(weighted_sell_value, 2),
            "trade_count": trade_count,
        },
        top_trades=top,
    )


async def analyze_insider_activity(
    pool,
    symbol: str,
    days: int = 90,
) -> InsiderSignal:
    # / fetch insider trades from db and analyze
    cutoff = date.today() - timedelta(days=days)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT symbol, filing_date, insider_name, insider_title,
                   transaction_type, shares, price_per_share, total_value
            FROM insider_trades
            WHERE symbol = $1 AND filing_date >= $2
            ORDER BY filing_date DESC
            """,
            symbol, cutoff,
        )

    trades = [dict(r) for r in rows]
    result = compute_insider_signal(trades, symbol)

    logger.info(
        "insider_analysis_complete",
        symbol=symbol,
        signal=result.signal,
        net_buy_ratio=result.net_buy_ratio,
        cluster=result.cluster_detected,
    )
    return result
