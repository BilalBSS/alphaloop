from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter

from src.dashboard.state import STATE

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/api/portfolio/correlation")
async def get_portfolio_correlation():
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


@router.get("/api/risk/sizing-multipliers")
async def get_sizing_multipliers():
    # / risk_limits.json passthrough
    import json
    from pathlib import Path
    path = Path(__file__).parent.parent.parent.parent / "configs" / "risk_limits.json"
    if not path.exists():
        return {"multipliers": {}}
    try:
        data = json.loads(path.read_text())
        return {"multipliers": data.get("regime_sizing_multipliers", {})}
    except Exception as exc:
        logger.debug("sizing_multipliers_load_failed", error=str(exc))
        return {"multipliers": {}}


@router.get("/api/risk/gauges")
async def get_risk_gauges():
    # / 4-up + 8-gate snapshot
    from pathlib import Path
    cfg_path = Path(__file__).parent.parent.parent.parent / "configs" / "risk_limits.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}

    gauges = {
        "var_95": {"value": None, "limit": None, "label": "VaR (95%)"},
        "tail_dep_lambda": {
            "value": None,
            "limit": float(cfg.get("tail_dependence_threshold", 0.30)),
            "label": "tail dep λ",
        },
        "drawdown_pct": {
            "value": None,
            "limit": float(cfg.get("max_drawdown_hard_stop", -0.20)),
            "label": "drawdown",
        },
        "gross_exposure_pct": {
            "value": None,
            "limit": 1.0 - float(cfg.get("min_cash_reserve_pct", 0.10)),
            "label": "gross exposure",
        },
    }

    try:
        broker = STATE.get_broker()
        positions = await broker.get_positions()
        balance = await broker.get_account_balance()
    except Exception as exc:
        logger.debug("risk_gauges_broker_failed", error=str(exc))
        return {"gauges": gauges, "gates": _gates(cfg, gauges, [], None)}

    equity = float(getattr(balance, "equity", 0) or 0)
    gross = sum(float(p.market_value or 0) for p in positions)
    if equity > 0:
        gauges["gross_exposure_pct"]["value"] = round(gross / equity, 4)

    if STATE.pool is not None:
        try:
            import numpy as np

            from src.quant.risk_metrics import var_historical
            async with STATE.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT date, equity FROM portfolio_snapshots
                    ORDER BY date DESC LIMIT 60""",
                )
            if rows and len(rows) >= 10:
                eq = [float(r["equity"]) for r in reversed(rows)]
                rets = np.diff(eq) / np.array(eq[:-1])
                var = float(var_historical(rets, confidence=0.95))
                gauges["var_95"]["value"] = round(var * equity, 2)
                gauges["var_95"]["limit"] = round(equity * 0.03, 2)
                peak = max(eq)
                trough = eq[-1]
                if peak > 0:
                    gauges["drawdown_pct"]["value"] = round((trough - peak) / peak, 4)
        except Exception as exc:
            logger.debug("risk_gauges_compute_failed", error=str(exc))

    try:
        td = await get_portfolio_tail_dependence()
        if isinstance(td, dict) and td.get("lambda_lower") is not None:
            gauges["tail_dep_lambda"]["value"] = float(td["lambda_lower"])
    except Exception as exc:
        logger.debug("risk_gauges_tail_dep_failed", error=str(exc))

    latest_per_signal = await _fetch_latest_per_signal_gates()
    return {"gauges": gauges, "gates": _gates(cfg, gauges, positions, equity, latest_per_signal)}


_PER_SIGNAL_GATES = ("strategy_exposure", "min_liquidity", "correlation_cluster")


async def _fetch_latest_per_signal_gates() -> dict[str, dict]:
    if STATE.pool is None:
        return {}
    try:
        async with STATE.pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT sizing_details, created_at
                FROM approved_trades
                WHERE sizing_details ? 'gates'
                ORDER BY created_at DESC LIMIT 1"""
            )
    except Exception as exc:
        logger.debug("per_signal_gates_query_failed", error=str(exc))
        return {}
    if not row:
        return {}
    sizing = row["sizing_details"]
    if isinstance(sizing, str):
        try:
            sizing = json.loads(sizing)
        except (TypeError, ValueError):
            return {}
    gates = sizing.get("gates") if isinstance(sizing, dict) else None
    if not isinstance(gates, list):
        return {}
    out: dict[str, dict] = {}
    ts = row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else None
    for g in gates:
        name = g.get("name") if isinstance(g, dict) else None
        if name in _PER_SIGNAL_GATES:
            out[name] = {
                "value": g.get("value"),
                "status": g.get("status", "pass"),
                "evaluated_at": ts,
            }
    return out


def _gates(
    cfg: dict, gauges: dict, positions: list, equity: float | None,
    latest_per_signal: dict[str, dict] | None = None,
) -> list[dict]:
    pos_count = len(positions)
    max_open = int(cfg.get("max_open_positions", 30))
    per_sig = latest_per_signal or {}
    strat_exp = per_sig.get("strategy_exposure", {})
    min_liq = per_sig.get("min_liquidity", {})
    corr_cl = per_sig.get("correlation_cluster", {})
    gates = [
        {
            "name": "position_count", "rule": f"positions ≤ {max_open}",
            "value": pos_count, "limit": max_open,
            "status": "pass" if pos_count <= max_open else "fail",
        },
        {
            "name": "tail_dependence",
            "rule": f"λ ≤ {cfg.get('tail_dependence_threshold', 0.30)}",
            "value": gauges["tail_dep_lambda"]["value"],
            "limit": gauges["tail_dep_lambda"]["limit"],
            "status": _cmp_pass(gauges["tail_dep_lambda"]["value"], gauges["tail_dep_lambda"]["limit"]),
        },
        {
            "name": "var_95",
            "rule": f"VaR(95) ≤ ${gauges['var_95']['limit']}",
            "value": gauges["var_95"]["value"],
            "limit": gauges["var_95"]["limit"],
            "status": _cmp_pass(gauges["var_95"]["value"], gauges["var_95"]["limit"]),
        },
        {
            "name": "drawdown_kill",
            "rule": f"DD > {cfg.get('max_drawdown_hard_stop', -0.20):.0%}",
            "value": gauges["drawdown_pct"]["value"],
            "limit": gauges["drawdown_pct"]["limit"],
            "status": _dd_pass(gauges["drawdown_pct"]["value"], gauges["drawdown_pct"]["limit"]),
        },
        {
            "name": "gross_exposure",
            "rule": f"≤ {gauges['gross_exposure_pct']['limit']:.0%}",
            "value": gauges["gross_exposure_pct"]["value"],
            "limit": gauges["gross_exposure_pct"]["limit"],
            "status": _cmp_pass(gauges["gross_exposure_pct"]["value"], gauges["gross_exposure_pct"]["limit"]),
        },
        {
            "name": "strategy_exposure",
            "rule": f"single strat ≤ {cfg.get('max_exposure_per_strategy_pct', 0.15):.0%}",
            "value": strat_exp.get("value"),
            "limit": float(cfg.get("max_exposure_per_strategy_pct", 0.15)),
            "status": strat_exp.get("status", "pending"),
            "evaluated_at": strat_exp.get("evaluated_at"),
        },
        {
            "name": "min_liquidity",
            "rule": f"≤ {cfg.get('max_liquidity_pct', 0.01):.0%} ADV",
            "value": min_liq.get("value"),
            "limit": float(cfg.get("max_liquidity_pct", 0.01)),
            "status": min_liq.get("status", "pending"),
            "evaluated_at": min_liq.get("evaluated_at"),
        },
        {
            "name": "correlation_cluster",
            "rule": "sum |corr| capped",
            "value": corr_cl.get("value"),
            "limit": 4.0,
            "status": corr_cl.get("status", "pending"),
            "evaluated_at": corr_cl.get("evaluated_at"),
        },
    ]
    return gates


def _cmp_pass(val, limit) -> str:
    if val is None or limit is None:
        return "pending"
    return "pass" if float(val) <= float(limit) else "fail"


def _dd_pass(val, limit) -> str:
    if val is None or limit is None:
        return "pending"
    return "pass" if float(val) >= float(limit) else "fail"


@router.get("/api/portfolio/tail-dependence")
async def get_portfolio_tail_dependence():
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

        # / per-position vs rest-of-portfolio
        per_position: list[dict] = []
        n_obs = returns.shape[0]
        for col in returns.columns:
            rest = returns.drop(columns=[col]).mean(axis=1)
            u_pair = np.column_stack([
                rankdata(returns[col].values) / (n_obs + 1),
                rankdata(rest.values) / (n_obs + 1),
            ])
            try:
                nu_p, corr_p = student_t_copula_fit(u_pair)
                td_p = tail_dependence_coefficient("student_t", (nu_p, corr_p))
                per_position.append({
                    "symbol": str(col),
                    "lambda": round(float(td_p.get("lambda_lower", 0.0)), 4),
                })
            except (ValueError, RuntimeError) as exc:
                logger.debug("per_position_lambda_failed", symbol=str(col), error=str(exc))
        per_position.sort(key=lambda x: x["lambda"], reverse=True)

        result = {
            "lambda_lower": round(float(lam), 4),
            "positions_count": len(positions),
            "status": "ok",
            "nu": round(float(nu), 2),
            "threshold": 0.30,
            "is_concentrated": lam > 0.30,
            "per_position": per_position,
        }
        STATE.tail_dep_cache.put(cache_key, result)
        return result
    except Exception as exc:
        logger.debug("tail_dependence_compute_failed", error=str(exc))
        return {"lambda_lower": None, "positions_count": 0, "status": "compute_failed"}
