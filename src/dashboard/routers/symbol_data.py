from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.dashboard.helpers import db, serializers
from src.dashboard.state import STATE

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/api/analysis/{symbol}")
async def get_analysis(symbol: str):
    # / full deep-dive: parallel fetch all symbol-scoped data
    (score, signals, trades, sentiment, fundamentals, dcf, market, social, insider, evolution) = await asyncio.gather(
        db.query_one(
            """SELECT * FROM analysis_scores
            WHERE symbol = $1 ORDER BY date DESC LIMIT 1""",
            symbol,
        ),
        db.query(
            """SELECT * FROM trade_signals
            WHERE symbol = $1 ORDER BY created_at DESC LIMIT 20""",
            symbol,
        ),
        db.query(
            """SELECT * FROM trade_log
            WHERE symbol = $1 ORDER BY created_at DESC LIMIT 20""",
            symbol,
        ),
        db.query(
            """SELECT date, sentiment_score, sentiment_label, source
            FROM news_sentiment WHERE symbol = $1
            ORDER BY date DESC LIMIT 30""",
            symbol,
        ),
        db.query_one(
            """SELECT f.*,
                s.avg_fcf_margin as sector_fcf_margin_avg,
                s.avg_de as sector_de_avg,
                s.avg_rev_growth as sector_rev_growth_avg
            FROM fundamentals f
            LEFT JOIN LATERAL (
                SELECT AVG(fcf_margin) as avg_fcf_margin,
                       AVG(debt_to_equity) as avg_de,
                       AVG(revenue_growth_1y) as avg_rev_growth
                FROM fundamentals f2
                WHERE f2.sector = f.sector AND f2.date = f.date AND f2.symbol != f.symbol
            ) s ON true
            WHERE f.symbol = $1 ORDER BY f.date DESC LIMIT 1""",
            symbol,
        ),
        db.query_one(
            """SELECT * FROM dcf_valuations
            WHERE symbol = $1 AND fair_value_median IS NOT NULL
            ORDER BY date DESC LIMIT 1""",
            symbol,
        ),
        db.query(
            """SELECT date, close, volume FROM market_data
            WHERE symbol = $1 ORDER BY date DESC LIMIT 60""",
            symbol,
        ),
        db.query(
            """SELECT date, source, bullish_pct, bearish_pct, volume, raw_score
            FROM social_sentiment WHERE symbol = $1
            ORDER BY date DESC LIMIT 30""",
            symbol,
        ),
        db.query(
            """SELECT filing_date, insider_name, insider_title, transaction_type,
                    shares, price_per_share, total_value
            FROM insider_trades WHERE symbol = $1
            ORDER BY filing_date DESC LIMIT 20""",
            symbol,
        ),
        db.query(
            """SELECT generation, action, strategy_id, reason, details, created_at
            FROM evolution_log
            WHERE strategy_id IN (
                SELECT DISTINCT strategy_id FROM trade_signals WHERE symbol = $1
            ) OR details::text LIKE '%' || $1 || '%'
            ORDER BY created_at DESC LIMIT 20""",
            symbol,
        ),
    )
    return {
        "score": serializers.serialize_one(score),
        "signals": serializers.serialize(signals),
        "trades": serializers.serialize(trades),
        "sentiment": serializers.serialize(sentiment),
        "social_sentiment": serializers.serialize(social),
        "fundamentals": serializers.serialize_one(fundamentals),
        "dcf": serializers.serialize_one(dcf),
        "price_history": serializers.serialize(market),
        "insider_trades": serializers.serialize(insider),
        "evolution": serializers.serialize(evolution),
    }


@router.get("/api/crypto-fundamentals/{symbol}")
async def get_crypto_fundamentals(symbol: str):
    from datetime import datetime, timezone

    from src.data.crypto_fundamentals import get_fundamentals
    from src.data.symbols import is_crypto
    sym = symbol.upper()
    if not is_crypto(sym):
        return JSONResponse({"error": "not a crypto symbol"}, status_code=400)
    try:
        return await get_fundamentals(STATE.pool, sym)
    except Exception as exc:
        logger.warning("crypto_fundamentals_endpoint_failed", symbol=sym, error=str(exc)[:200])
        return {
            "nvt_ratio": None,
            "funding_rate": None,
            "active_addresses": None,
            "exchange_inflow_usd": None,
            "hash_rate": None,
            "tvl_usd": None,
            "dex_volume_24h": None,
            "stablecoin_supply_ratio": None,
            "sources": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/api/insider/{symbol}")
async def get_insider(symbol: str):
    sym = symbol.upper()
    rows = await db.query(
        """SELECT * FROM insider_trades
        WHERE symbol = $1 AND filing_date > CURRENT_DATE - INTERVAL '90 days'
        ORDER BY filing_date DESC LIMIT 20""",
        sym,
    )
    latest_score = await db.query_one(
        """SELECT details->>'insider_signed_strength' AS signed_strength,
                  details->>'insider_score_100' AS score_100
        FROM analysis_scores
        WHERE symbol = $1
        ORDER BY date DESC LIMIT 1""",
        sym,
    )
    signed = None
    score_100 = None
    if latest_score:
        try:
            raw_signed = latest_score.get("signed_strength")
            signed = float(raw_signed) if raw_signed is not None else None
        except (TypeError, ValueError):
            signed = None
        try:
            raw_score = latest_score.get("score_100")
            score_100 = float(raw_score) if raw_score is not None else None
        except (TypeError, ValueError):
            score_100 = None
    signal = "neutral"
    if signed is not None:
        if signed > 10:
            signal = "bullish"
        elif signed < -10:
            signal = "bearish"
    return {
        "trades": serializers.serialize(rows),
        "signed_strength": signed,
        "score_100": score_100,
        "signal": signal,
    }


@router.get("/api/synthesis")
async def get_synthesis():
    row = await db.query_one(
        "SELECT * FROM daily_synthesis ORDER BY date DESC LIMIT 1"
    )
    return serializers.serialize_one(row)


@router.get("/api/ict-indicators/{symbol}")
async def get_ict_indicators(symbol: str):
    rows = await db.query(
        """SELECT ict_data FROM computed_indicators
        WHERE symbol = $1 AND timeframe = '1Day' AND ict_data IS NOT NULL
        ORDER BY date DESC LIMIT 1""",
        symbol,
    )
    if rows and rows[0].get("ict_data"):
        data = rows[0]["ict_data"]
        if isinstance(data, dict):
            return data
        return json.loads(data) if isinstance(data, str) else {}
    return {"fvgs": [], "order_blocks": [], "structure_breaks": []}


@router.get("/api/quant-metrics/{symbol}")
async def get_quant_metrics(symbol: str):
    rows = await db.query(
        """SELECT DISTINCT ON (ss.strategy_id) ss.*
        FROM strategy_scores ss
        WHERE ss.strategy_id IN (
            SELECT strategy_id FROM trade_signals WHERE symbol = $1 AND strategy_id IS NOT NULL
            UNION
            SELECT strategy_id FROM trade_log WHERE symbol = $1 AND strategy_id IS NOT NULL
            UNION
            SELECT strategy_id FROM strategy_positions WHERE symbol = $1
              AND strategy_id IS NOT NULL AND strategy_id <> 'untracked' AND qty > 0
        )
        ORDER BY ss.strategy_id, ss.created_at DESC""",
        symbol,
    )
    rows_sorted = sorted(
        rows,
        key=lambda r: float(r.get("sharpe_ratio") or -999),
        reverse=True,
    )
    return serializers.serialize(rows_sorted)


@router.get("/api/congressional/{symbol}")
async def get_congressional(symbol: str):
    sym = symbol.upper()
    if STATE.pool is None:
        return {"trades": [], "net_buy_ratio": 0.0}
    rows = await db.query(
        """SELECT filing_date, name, transaction_type, amount_range
        FROM congressional_trades
        WHERE symbol = $1
        ORDER BY filing_date DESC LIMIT 20""",
        sym,
    )
    from src.data.congressional_trades import compute_net_buy_ratio
    ratio = compute_net_buy_ratio(rows) if rows else 0.0
    return {
        "trades": serializers.serialize(rows),
        "net_buy_ratio": round(float(ratio), 3),
    }


@router.get("/api/analyst-ratings/{symbol}")
async def get_analyst_ratings(symbol: str):
    sym = symbol.upper()
    if STATE.pool is None:
        return {"history": []}
    rows = await db.query(
        """SELECT date, strong_buy, buy, hold, sell, strong_sell,
                target_high, target_low, target_mean
        FROM analyst_ratings
        WHERE symbol = $1
        ORDER BY date DESC LIMIT 60""",
        sym,
    )
    from src.data.analyst_ratings import compute_consensus_score
    history = []
    for r in rows:
        rec = {
            "strongBuy": r.get("strong_buy") or 0,
            "buy": r.get("buy") or 0,
            "hold": r.get("hold") or 0,
            "sell": r.get("sell") or 0,
            "strongSell": r.get("strong_sell") or 0,
        }
        consensus = compute_consensus_score(rec)
        serialized = serializers.serialize_one(r)
        if serialized is not None:
            serialized["consensus_score"] = round(consensus, 3)
            history.append(serialized)
    return {"history": history}


@router.get("/api/options/{symbol}")
async def get_options(symbol: str):
    sym = symbol.upper()
    if STATE.pool is None:
        return {"history": [], "latest": None}
    rows = await db.query(
        """SELECT date, iv_current, iv_rank, put_call_ratio, max_pain
        FROM options_data
        WHERE symbol = $1
        ORDER BY date DESC LIMIT 60""",
        sym,
    )
    serialized = serializers.serialize(rows)
    latest = serialized[0] if serialized else None
    return {"history": serialized, "latest": latest}


@router.get("/api/short/{symbol}")
async def get_short(symbol: str):
    sym = symbol.upper()
    if STATE.pool is None:
        return {"history": [], "latest": None}
    rows = await db.query(
        """SELECT date, short_volume, total_volume, short_ratio
        FROM short_interest
        WHERE symbol = $1
        ORDER BY date DESC LIMIT 60""",
        sym,
    )
    serialized = serializers.serialize(rows)
    latest = serialized[0] if serialized else None
    return {"history": serialized, "latest": latest}


@router.get("/api/dark-pool/{symbol}")
async def get_dark_pool(symbol: str):
    sym = symbol.upper()
    if STATE.pool is None:
        return {"history": [], "latest": None}
    rows = await db.query(
        """SELECT week_start, ats_volume, total_volume, dark_pool_ratio
        FROM dark_pool
        WHERE symbol = $1
        ORDER BY week_start DESC LIMIT 26""",
        sym,
    )
    serialized = serializers.serialize(rows)
    latest = serialized[0] if serialized else None
    return {"history": serialized, "latest": latest}


@router.get("/api/earnings-revisions/{symbol}")
async def get_earnings_revisions(symbol: str):
    sym = symbol.upper()
    if STATE.pool is None:
        return {"history": [], "momentum": 0.0}
    rows = await db.query(
        """SELECT period, estimate_date, eps_estimate, revenue_estimate
        FROM earnings_revisions
        WHERE symbol = $1
        ORDER BY estimate_date DESC LIMIT 40""",
        sym,
    )
    from src.data.earnings_revisions import compute_revision_momentum
    estimates = [
        {"eps_avg": float(r["eps_estimate"]) if r.get("eps_estimate") is not None else None}
        for r in rows
    ]
    momentum = compute_revision_momentum(estimates) if estimates else 0.0
    return {
        "history": serializers.serialize(rows),
        "momentum": round(float(momentum), 3),
    }
