# / unified marker aggregator — pulls timeline events for a symbol from disparate tables
# / returns a dict keyed by marker kind: trades, signals, insiders, earnings, regime, consensus
# / caller filters by kinds param; shapes designed for lightweight-charts setMarkers() + priceLines
# / each fetch guards against missing tables on fresh databases via asyncpg.PostgresError

from __future__ import annotations

from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

# / signals below this strength are considered noise and dropped from the chart
_SIGNAL_STRENGTH_MIN = 0.5

# / insider cluster detection window + minimum cluster size
_INSIDER_CLUSTER_DAYS = 5
_INSIDER_CLUSTER_MIN = 3


def _iso(value: Any) -> str | None:
    # / uniform iso timestamp rendering for jsonable output
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _num(value: Any) -> float | None:
    # / safe numeric coerce — handles Decimal/None/str without raising
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def fetch_trade_markers(pool: asyncpg.Pool, symbol: str, interval: str) -> list[dict]:
    # / closed + open trades from trade_log filtered to recent window
    # / trade_log stores one row per fill with side + price, no explicit exit_price
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT created_at, symbol, side, price, strategy_id, pnl
                FROM trade_log
                WHERE symbol = $1 AND created_at > NOW() - $2::INTERVAL
                ORDER BY created_at ASC""",
                symbol,
                interval,
            )
    except asyncpg.PostgresError as exc:
        logger.debug("marker_trades_query_failed", symbol=symbol, error=str(exc))
        return []
    out: list[dict] = []
    for r in rows:
        side = (r.get("side") or "").lower()
        out.append({
            "time": _iso(r.get("created_at")),
            "price": _num(r.get("price")),
            "side": "buy" if side in ("buy", "long") else "sell",
            "strategy_id": r.get("strategy_id"),
            "pnl": _num(r.get("pnl")),
        })
    return out


async def fetch_signal_markers(pool: asyncpg.Pool, symbol: str, interval: str) -> list[dict]:
    # / recent trade signals above strength threshold
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT created_at, signal_type, strength, strategy_id
                FROM trade_signals
                WHERE symbol = $1 AND created_at > NOW() - $2::INTERVAL
                ORDER BY created_at ASC""",
                symbol,
                interval,
            )
    except asyncpg.PostgresError as exc:
        logger.debug("marker_signals_query_failed", symbol=symbol, error=str(exc))
        return []
    out: list[dict] = []
    for r in rows:
        strength = _num(r.get("strength"))
        if strength is None or strength < _SIGNAL_STRENGTH_MIN:
            continue
        sig_type = (r.get("signal_type") or "").lower()
        action = "buy" if sig_type == "buy" else "sell"
        out.append({
            "time": _iso(r.get("created_at")),
            "action": action,
            "strength": strength,
            "strategy_id": r.get("strategy_id"),
        })
    return out


async def fetch_insider_markers(pool: asyncpg.Pool, symbol: str, interval: str) -> list[dict]:
    # / recent insider form 4 filings, aggregated into clusters when 3+ fire within 5 days
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT filing_date, insider_name, transaction_type, shares
                FROM insider_trades
                WHERE symbol = $1 AND filing_date > (NOW() - $2::INTERVAL)::DATE
                ORDER BY filing_date ASC""",
                symbol,
                interval,
            )
    except asyncpg.PostgresError as exc:
        logger.debug("marker_insiders_query_failed", symbol=symbol, error=str(exc))
        return []
    # / normalize rows, drop any with missing date/type
    events: list[dict] = []
    for r in rows:
        fdate = r.get("filing_date")
        ttype = (r.get("transaction_type") or "").lower()
        if fdate is None or ttype not in ("buy", "sell"):
            continue
        events.append({
            "time": _iso(fdate),
            "transaction_type": ttype,
            "shares": _num(r.get("shares")) or 0.0,
            "name": r.get("insider_name"),
            "_date": fdate,
        })
    # / cluster by transaction type within a rolling 5-day window
    clusters: list[dict] = []
    used = [False] * len(events)
    for i, ev in enumerate(events):
        if used[i]:
            continue
        window = [ev]
        used[i] = True
        for j in range(i + 1, len(events)):
            if used[j]:
                continue
            other = events[j]
            if other["transaction_type"] != ev["transaction_type"]:
                continue
            diff = (other["_date"] - ev["_date"]).days
            if 0 <= diff <= _INSIDER_CLUSTER_DAYS:
                window.append(other)
                used[j] = True
        if len(window) >= _INSIDER_CLUSTER_MIN:
            total_shares = sum(w["shares"] for w in window)
            names = ", ".join(sorted({w["name"] for w in window if w["name"]}))
            clusters.append({
                "time": ev["time"],
                "transaction_type": ev["transaction_type"],
                "shares": total_shares,
                "name": names or f"{len(window)} insiders",
                "cluster_size": len(window),
            })
        else:
            for w in window:
                clusters.append({
                    "time": w["time"],
                    "transaction_type": w["transaction_type"],
                    "shares": w["shares"],
                    "name": w["name"],
                    "cluster_size": 1,
                })
    return clusters


async def fetch_earnings_markers(pool: asyncpg.Pool, symbol: str, interval: str) -> list[dict]:
    # / uses earnings_revisions (not earnings_surprises) — stores sell-side estimate revisions
    # / type classification: compare current estimate against prior estimate for the same period
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT estimate_date, period, eps_estimate, revenue_estimate
                FROM earnings_revisions
                WHERE symbol = $1 AND estimate_date > (NOW() - $2::INTERVAL)::DATE
                ORDER BY estimate_date ASC""",
                symbol,
                interval,
            )
    except asyncpg.PostgresError as exc:
        logger.debug("marker_earnings_query_failed", symbol=symbol, error=str(exc))
        return []
    # / compute prior-estimate delta per period so we can label beat/miss/inline vs prior revision
    prior: dict[str, float] = {}
    out: list[dict] = []
    for r in rows:
        period = r.get("period") or ""
        eps = _num(r.get("eps_estimate"))
        prev = prior.get(period)
        label = "inline"
        if eps is not None and prev is not None:
            if eps > prev * 1.01:
                label = "beat"
            elif eps < prev * 0.99:
                label = "miss"
        out.append({
            "time": _iso(r.get("estimate_date")),
            "type": label,
            "eps_actual": eps,
            "eps_estimate": prev,
            "period": period,
        })
        if eps is not None:
            prior[period] = eps
    return out


def _market_for_symbol(symbol: str) -> str:
    # / crypto symbols on alpaca carry a -USD suffix (BTC-USD, ETH-USD, HYPE-USD)
    # / stocks are plain tickers (AAPL, MSFT). route the regime query to the correct market
    if not isinstance(symbol, str):
        return "equity"
    s = symbol.upper()
    if s.endswith("-USD") or s.endswith("/USD") or s.endswith("USDT"):
        return "crypto"
    return "equity"


async def fetch_regime_bands(pool: asyncpg.Pool, symbol: str, interval: str) -> list[dict]:
    # / market-wide regime windows from regime_history; emits [start,end) intervals
    # / market is inferred from the symbol so crypto charts don't surface equity regimes
    market = _market_for_symbol(symbol)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT date, regime
                FROM regime_history
                WHERE market = $1 AND date > (NOW() - $2::INTERVAL)::DATE
                ORDER BY date ASC""",
                market,
                interval,
            )
    except asyncpg.PostgresError as exc:
        logger.debug("marker_regime_query_failed", error=str(exc))
        return []
    # / collapse consecutive same-regime days into single bands
    bands: list[dict] = []
    cur_start: Any = None
    cur_regime: str | None = None
    last_date: Any = None
    for r in rows:
        regime = (r.get("regime") or "").lower() or None
        date_val = r.get("date")
        if regime is None or date_val is None:
            continue
        if cur_regime is None:
            cur_regime = regime
            cur_start = date_val
            last_date = date_val
            continue
        if regime != cur_regime:
            bands.append({
                "start": _iso(cur_start),
                "end": _iso(last_date),
                "regime": cur_regime,
            })
            cur_regime = regime
            cur_start = date_val
        last_date = date_val
    if cur_regime is not None and cur_start is not None:
        bands.append({
            "start": _iso(cur_start),
            "end": _iso(last_date),
            "regime": cur_regime,
        })
    return bands


async def fetch_consensus_strip(pool: asyncpg.Pool, symbol: str, interval: str) -> list[dict]:
    # / per-snapshot dual-llm consensus from analysis_scores.details jsonb
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT date, details->>'ai_consensus' as consensus
                FROM analysis_scores
                WHERE symbol = $1 AND date > (NOW() - $2::INTERVAL)::DATE
                ORDER BY date ASC""",
                symbol,
                interval,
            )
    except asyncpg.PostgresError as exc:
        logger.debug("marker_consensus_query_failed", symbol=symbol, error=str(exc))
        return []
    out: list[dict] = []
    for r in rows:
        consensus = (r.get("consensus") or "").lower() or None
        if consensus not in ("bullish", "bearish", "neutral", "disagree"):
            continue
        out.append({
            "time": _iso(r.get("date")),
            "consensus": consensus,
        })
    return out


async def build_markers(
    pool: asyncpg.Pool,
    symbol: str,
    kinds: set[str],
    days: int,
) -> dict[str, list[dict]]:
    # / orchestrates the selected kinds against the aggregators, returning a dict keyed by kind
    # / missing kinds are absent from the result so callers can detect the filter applied
    interval = f"{int(days)} days"
    out: dict[str, list[dict]] = {}
    if "trades" in kinds:
        out["trades"] = await fetch_trade_markers(pool, symbol, interval)
    if "signals" in kinds:
        out["signals"] = await fetch_signal_markers(pool, symbol, interval)
    if "insiders" in kinds:
        out["insiders"] = await fetch_insider_markers(pool, symbol, interval)
    if "earnings" in kinds:
        out["earnings"] = await fetch_earnings_markers(pool, symbol, interval)
    if "regime" in kinds:
        out["regime"] = await fetch_regime_bands(pool, symbol, interval)
    if "consensus" in kinds:
        out["consensus"] = await fetch_consensus_strip(pool, symbol, interval)
    return out
