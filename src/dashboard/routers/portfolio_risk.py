from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter

from src.dashboard.state import STATE

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/api/portfolio/correlation")
async def get_portfolio_correlation():
    # / pairwise correlation matrix of held positions via correlation_monitor
    try:
        broker = STATE.get_broker()
        positions = await broker.get_positions()
    except Exception as exc:
        logger.debug("portfolio_correlation_broker_failed", error=str(exc))
        return {"symbols": [], "matrix": [], "avg_correlation": 0.0, "is_concentrated": False}
    if STATE.pool is None or len(positions) < 2:
        return {"symbols": [s.symbol for s in positions], "matrix": [], "avg_correlation": 0.0, "is_concentrated": False}
    try:
        import numpy as np

        from src.quant.correlation_monitor import check_portfolio_correlation
        symbols = [p.symbol for p in positions]
        returns_map: dict[str, list[float]] = {}
        async with STATE.pool.acquire() as conn:
            for sym in symbols:
                rows = await conn.fetch(
                    """SELECT close FROM market_data
                    WHERE symbol = $1 ORDER BY date DESC LIMIT 21""",
                    sym,
                )
                if len(rows) >= 10:
                    prices = [float(r["close"]) for r in reversed(rows)]
                    rets = np.diff(prices) / np.array(prices[:-1])
                    returns_map[sym] = rets.tolist()
        if len(returns_map) < 2:
            return {"symbols": symbols, "matrix": [], "avg_correlation": 0.0, "is_concentrated": False}
        min_len = min(len(r) for r in returns_map.values())
        aligned_syms = list(returns_map.keys())
        aligned = np.array([returns_map[s][-min_len:] for s in aligned_syms])
        matrix = np.corrcoef(aligned).tolist()
        alert = await check_portfolio_correlation(STATE.pool, positions)
        return {
            "symbols": aligned_syms,
            "matrix": [[round(float(v), 3) for v in row] for row in matrix],
            "avg_correlation": round(alert.avg_correlation, 3) if alert else 0.0,
            "max_pair": list(alert.max_pair) if alert else [],
            "max_correlation": round(alert.max_correlation, 3) if alert else 0.0,
            "is_concentrated": alert.is_concentrated if alert else False,
        }
    except Exception as exc:
        logger.debug("portfolio_correlation_compute_failed", error=str(exc))
        return {"symbols": [], "matrix": [], "avg_correlation": 0.0, "is_concentrated": False}


@router.get("/api/portfolio/sectors")
async def get_portfolio_sectors():
    # / sector concentration from broker positions
    from src.data.symbols import get_sector
    if STATE.pool is None:
        return {"sectors": [], "total_value": 0.0}
    try:
        broker = STATE.get_broker()
        positions = await broker.get_positions()
        total_value = sum(float(p.market_value or 0) for p in positions)
        by_sector: dict[str, float] = {}
        for p in positions:
            sec = get_sector(p.symbol) or "unknown"
            by_sector[sec] = by_sector.get(sec, 0.0) + float(p.market_value or 0)
        sectors = [
            {
                "sector": sec,
                "value": round(val, 2),
                "pct_of_portfolio": round(val / total_value, 4) if total_value > 0 else 0.0,
            }
            for sec, val in sorted(by_sector.items(), key=lambda kv: kv[1], reverse=True)
        ]
        return {"sectors": sectors, "total_value": round(total_value, 2)}
    except Exception as exc:
        logger.debug("portfolio_sectors_failed", error=str(exc))
        return {"sectors": [], "total_value": 0.0}


@router.get("/api/portfolio/tail-dependence")
async def get_portfolio_tail_dependence():
    # / t-copula tail-dependence on held positions; 5min cache by (positions tuple, utc date)
    if STATE.pool is None:
        return {"lambda_lower": None, "positions_count": 0, "status": "pool_unavailable"}
    try:
        broker = STATE.get_broker()
        positions = await broker.get_positions()
        if len(positions) < 2:
            return {"lambda_lower": None, "positions_count": len(positions), "status": "insufficient_positions"}

        position_symbols = sorted(p.symbol for p in positions)
        today = datetime.now(timezone.utc).date()
        cache_key = (tuple(position_symbols), today)
        cached = STATE.tail_dep_cache.get(cache_key)
        if cached is not None:
            return cached

        import numpy as np
        from scipy.stats import rankdata

        from src.quant.copula_models import student_t_copula_fit, tail_dependence_coefficient

        async with STATE.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT symbol, date, close FROM market_data
                WHERE symbol = ANY($1)
                ORDER BY date DESC LIMIT $2""",
                position_symbols, 252 * len(position_symbols),
            )
        if not rows:
            return {"lambda_lower": None, "positions_count": len(positions), "status": "no_data"}
        import pandas as pd
        df = pd.DataFrame([dict(r) for r in rows])
        pivot = df.pivot_table(index="date", columns="symbol", values="close")
        if pivot.shape[0] < 10 or pivot.shape[1] < 2:
            return {"lambda_lower": None, "positions_count": len(positions), "status": "insufficient_history"}
        returns = pivot.pct_change().dropna()
        if returns.shape[0] < 10:
            return {"lambda_lower": None, "positions_count": len(positions), "status": "insufficient_returns"}
        u_data = np.column_stack([
            rankdata(returns.iloc[:, j]) / (returns.shape[0] + 1)
            for j in range(returns.shape[1])
        ])
        nu, corr = student_t_copula_fit(u_data)
        td = tail_dependence_coefficient("student_t", (nu, corr))
        lam = td.get("lambda_lower", 0.0)
        result = {
            "lambda_lower": round(float(lam), 4),
            "positions_count": len(positions),
            "status": "ok",
            "nu": round(float(nu), 2),
            "threshold": 0.30,
            "is_concentrated": lam > 0.30,
        }
        STATE.tail_dep_cache.put(cache_key, result)
        return result
    except Exception as exc:
        logger.debug("tail_dependence_compute_failed", error=str(exc))
        return {"lambda_lower": None, "positions_count": 0, "status": "compute_failed"}
