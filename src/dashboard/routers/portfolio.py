from __future__ import annotations

import asyncio
import json
from pathlib import Path

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.dashboard.helpers import db, serializers
from src.dashboard.state import STATE
from src.data.symbols import get_sector

logger = structlog.get_logger(__name__)

router = APIRouter()

STRATEGY_CONFIGS_DIR = (Path(__file__).parent.parent.parent.parent / "configs" / "strategies").resolve()
_RISK_LIMITS_PATH = Path(__file__).parent.parent.parent.parent / "configs" / "risk_limits.json"


def _enrich_position(p, strategy_map: dict[str, str], opened_map: dict[str, str]) -> dict:
    return {
        "symbol": p.symbol,
        "side": p.side,
        "qty": p.qty,
        "market_value": p.market_value,
        "entry_price": p.avg_entry_price,
        "unrealized_pl": p.unrealized_pnl,
        "current_price": p.current_price,
        "strategy_id": strategy_map.get(p.symbol),
        "sector": get_sector(p.symbol),
        "opened_at": opened_map.get(p.symbol),
    }


async def _strategy_map_for_positions(symbols: list[str]) -> dict[str, str]:
    if not symbols:
        return {}
    rows = await db.query(
        """SELECT DISTINCT ON (symbol) symbol, strategy_id
        FROM strategy_positions
        WHERE symbol = ANY($1) AND qty > 0
          AND strategy_id IS NOT NULL
          AND lower(strategy_id) <> 'untracked'
        ORDER BY symbol, updated_at DESC""",
        symbols,
    )
    return {r["symbol"]: r["strategy_id"] for r in rows}


async def _opened_at_map(symbols: list[str]) -> dict[str, str]:
    if not symbols:
        return {}
    rows = await db.query(
        """SELECT symbol, MIN(created_at) AS opened_at
        FROM trade_log
        WHERE symbol = ANY($1) AND side = 'buy'
        GROUP BY symbol""",
        symbols,
    )
    return {
        r["symbol"]: r["opened_at"].isoformat()
        for r in rows
        if r.get("opened_at") is not None
    }


async def _latest_regime() -> tuple[str | None, float | None]:
    row = await db.query_one(
        """SELECT regime, confidence FROM regime_history
        WHERE market = 'equity' ORDER BY date DESC LIMIT 1"""
    )
    if not row:
        return None, None
    conf = row.get("confidence")
    try:
        conf_f = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        conf_f = None
    return row.get("regime"), conf_f


async def _next_strategy_cycle_ts() -> str | None:
    if STATE.pool is None:
        return None
    try:
        from src.data.loop_registry import describe_loops
        loops = await describe_loops(STATE.pool)
    except Exception as exc:
        logger.debug("loops_lookup_failed", error=str(exc))
        return None
    target = next(
        (loop for loop in loops if loop.get("name") in ("strategy_eval", "analyst")),
        None,
    )
    if not target:
        return None
    nft = target.get("next_fire_ts")
    if nft is None:
        return None
    return nft.isoformat() if hasattr(nft, "isoformat") else str(nft)


def _risk_budget_used(
    var_95: float | None,
    drawdown: float | None,
    gross_exposure_pct: float | None,
    tail_dep: float | None,
    cfg: dict,
) -> float | None:
    var_limit_pct = 0.03
    dd_limit = float(cfg.get("max_drawdown_hard_stop", -0.20))
    gross_cap = 1.0 - float(cfg.get("min_cash_reserve_pct", 0.10))
    tail_threshold = float(cfg.get("tail_dependence_threshold", 0.30))
    ratios: list[float] = []
    if var_95 is not None and var_limit_pct > 0:
        ratios.append(min(1.0, abs(float(var_95)) / var_limit_pct))
    if drawdown is not None and dd_limit:
        ratios.append(min(1.0, abs(float(drawdown)) / abs(dd_limit)))
    if gross_exposure_pct is not None and gross_cap > 0:
        ratios.append(min(1.0, float(gross_exposure_pct) / gross_cap))
    if tail_dep is not None and tail_threshold > 0:
        ratios.append(min(1.0, float(tail_dep) / tail_threshold))
    if not ratios:
        return None
    return round(max(ratios), 3)


async def _drawdown_and_var_pct() -> tuple[float | None, float | None]:
    if STATE.pool is None:
        return None, None
    try:
        import numpy as np

        from src.quant.risk_metrics import var_historical
        async with STATE.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT date, equity FROM portfolio_snapshots
                ORDER BY date DESC LIMIT 60"""
            )
        if not rows or len(rows) < 10:
            return None, None
        eq = [float(r["equity"]) for r in reversed(rows)]
        rets = np.diff(eq) / np.array(eq[:-1])
        var_pct = float(var_historical(rets, confidence=0.95))
        peak = max(eq)
        dd = (eq[-1] - peak) / peak if peak > 0 else None
        return dd, var_pct
    except Exception as exc:
        logger.debug("drawdown_var_compute_failed", error=str(exc))
        return None, None


@router.get("/api/portfolio")
async def get_portfolio():
    try:
        broker = STATE.get_broker()
        balance, positions = await asyncio.gather(
            broker.get_account_balance(),
            broker.get_positions(),
        )
        symbols = [p.symbol for p in positions]
        trades_q = db.query(
            """SELECT * FROM trade_log
            WHERE created_at >= CURRENT_DATE ORDER BY created_at DESC"""
        )
        regime_q = _latest_regime()
        cycle_q = _next_strategy_cycle_ts()
        strat_q = _strategy_map_for_positions(symbols)
        opened_q = _opened_at_map(symbols)
        dd_var_q = _drawdown_and_var_pct()
        trades_today_raw, regime_pair, next_cycle_ts, strategy_map, opened_map, dd_var = await asyncio.gather(
            trades_q, regime_q, cycle_q, strat_q, opened_q, dd_var_q,
        )
        trades_today = serializers.serialize(trades_today_raw)
        unrealized = sum(p.unrealized_pnl for p in positions)
        realized = 0.0
        for t in trades_today:
            pnl = t.get("pnl")
            if pnl is None:
                continue
            try:
                realized += float(pnl)
            except (TypeError, ValueError):
                continue
        regime, regime_conf = regime_pair
        drawdown_pct, var_pct = dd_var
        equity = float(balance.equity or 0)
        gross = sum(float(p.market_value or 0) for p in positions)
        gross_exposure_pct = round(gross / equity, 4) if equity > 0 else None
        cfg: dict = {}
        try:
            cfg = json.loads(_RISK_LIMITS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.debug("risk_limits_read_failed", error=str(exc))
        max_open = int(cfg.get("max_open_positions", 30)) if cfg else 30
        return {
            "equity": balance.equity,
            "cash": balance.cash,
            "buying_power": balance.buying_power,
            "positions_count": len(positions),
            "max_open_positions": max_open,
            "daily_pnl": unrealized + realized,
            "regime": regime,
            "regime_confidence": regime_conf,
            "next_cycle_ts": next_cycle_ts,
            "drawdown": drawdown_pct,
            "var_95_pct": var_pct,
            "gross_exposure_pct": gross_exposure_pct,
            "risk_budget_used": _risk_budget_used(
                var_pct, drawdown_pct, gross_exposure_pct, None, cfg or {},
            ),
            "positions": [_enrich_position(p, strategy_map, opened_map) for p in positions],
            "trades_today": trades_today,
        }
    except Exception as exc:
        logger.debug("portfolio_alpaca_fallback", error=str(exc))
        positions = await db.query(
            """SELECT symbol, side, qty, price, strategy_id, created_at
            FROM trade_log ORDER BY created_at DESC LIMIT 50"""
        )
        return {"positions_count": 0, "positions": serializers.serialize(positions), "trades_today": []}


@router.get("/api/equity-history")
async def get_equity_history(period: str = "1D", timeframe: str = "5Min"):
    from src.data.alpaca_client import alpaca_base_url, alpaca_headers, get_alpaca_client
    base = alpaca_base_url()
    headers = alpaca_headers()
    try:
        client = await get_alpaca_client()
        resp = await client.get(
            f"{base}/v2/account/portfolio/history",
            headers=headers,
            params={"period": period, "timeframe": timeframe, "intraday_reporting": "market_hours", "pnl_reset": "per_day"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        timestamps = data.get("timestamp", [])
        equity = data.get("equity", [])
        profit_loss = data.get("profit_loss", [])
        points = [
            {"ts": ts, "equity": eq}
            for ts, eq in zip(timestamps, equity, strict=False)
            if eq is not None
        ]
        return {
            "timestamps": timestamps,
            "equity": equity,
            "profit_loss": profit_loss,
            "points": points,
            "base_value": data.get("base_value", 100000),
        }
    except Exception as exc:
        logger.debug("equity_history_failed", error=str(exc))
        return {"timestamps": [], "equity": [], "profit_loss": [], "points": [], "base_value": 100000}


@router.get("/api/strategy-positions")
async def get_strategy_positions(symbol: str | None = None):
    if symbol:
        rows = await db.query(
            """SELECT strategy_id, symbol, qty, avg_entry_price, updated_at
            FROM strategy_positions WHERE symbol = $1
            ORDER BY strategy_id""",
            symbol,
        )
    else:
        rows = await db.query(
            """SELECT strategy_id, symbol, qty, avg_entry_price, updated_at
            FROM strategy_positions ORDER BY symbol, strategy_id"""
        )
    return serializers.serialize(rows)


@router.get("/api/trades")
async def get_trades(limit: int = 100, offset: int = 0, symbol: str | None = None):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    if symbol:
        rows = await db.query(
            """SELECT * FROM trade_log WHERE symbol = $1
            ORDER BY created_at DESC LIMIT $2 OFFSET $3""",
            symbol, limit, offset,
        )
    else:
        rows = await db.query(
            """SELECT * FROM trade_log
            ORDER BY created_at DESC LIMIT $1 OFFSET $2""",
            limit, offset,
        )
    return serializers.serialize(rows)


@router.get("/api/trades/{trade_id}/detail")
async def get_trade_detail(trade_id: int):
    trade = await db.query_one(
        "SELECT * FROM trade_log WHERE id = $1", trade_id,
    )
    if not trade:
        return JSONResponse({"error": "trade not found"}, status_code=404)
    signal = None
    approved = None
    if trade.get("trade_id") is not None:
        approved = await db.query_one(
            "SELECT * FROM approved_trades WHERE id = $1", trade["trade_id"],
        )
        if approved and approved.get("signal_id") is not None:
            signal = await db.query_one(
                "SELECT * FROM trade_signals WHERE id = $1", approved["signal_id"],
            )
    analysis = await db.query_one(
        """SELECT date, composite_score, fundamental_score, technical_score,
                regime, regime_confidence, details
        FROM analysis_scores
        WHERE symbol = $1 AND date <= $2::date
        ORDER BY date DESC LIMIT 1""",
        trade["symbol"], trade.get("created_at"),
    )
    return {
        "trade": serializers.serialize_one(trade),
        "signal": serializers.serialize_one(signal) if signal else None,
        "approved": serializers.serialize_one(approved) if approved else None,
        "analysis": serializers.serialize_one(analysis) if analysis else None,
    }


@router.get("/api/strategies")
async def get_strategies():
    strategies_by_id: dict = {}
    for config_path in sorted(STRATEGY_CONFIGS_DIR.glob("*.json")):
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            sid = cfg.get("id", config_path.stem)
            entry_signals = cfg.get("entry_conditions", {}).get("signals", [])
            exit_conds = cfg.get("exit_conditions", {})
            meta = cfg.get("metadata", {}) or {}
            backtest_sharpe = meta.get("backtest_sharpe")
            backtest_mdd = meta.get("backtest_max_drawdown")
            backtest_win_rate = meta.get("backtest_win_rate")
            backtest_brier = meta.get("brier_score")
            backtest_trade_count = meta.get("trade_count")
            strategies_by_id[sid] = {
                "strategy_id": sid,
                "name": cfg.get("name"),
                "status": meta.get("status"),
                "description": cfg.get("description"),
                "universe": cfg.get("universe"),
                "asset_class": cfg.get("asset_class"),
                "entry_conditions_count": len(entry_signals),
                "exit_conditions_count": len(exit_conds),
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0,
                "avg_pnl": 0,
                "win_rate": backtest_win_rate,
                "sharpe_ratio": backtest_sharpe,
                "max_drawdown": backtest_mdd,
                "brier_score": backtest_brier,
                "last_trade_at": None,
                "backtest_sharpe": backtest_sharpe,
                "backtest_max_drawdown": backtest_mdd,
                "backtest_win_rate": backtest_win_rate,
                "backtest_brier": backtest_brier,
                "backtest_trade_count": backtest_trade_count,
                "metrics_source": "backtest" if backtest_sharpe is not None else None,
            }
        except Exception as exc:
            logger.warning("strategy_config_read_failed", path=str(config_path), error=str(exc))

    score_rows = await db.query(
        """SELECT DISTINCT ON (strategy_id) *
        FROM strategy_scores
        ORDER BY strategy_id, created_at DESC"""
    )
    for row in score_rows:
        sid = row.get("strategy_id")
        if sid and sid in strategies_by_id:
            strategies_by_id[sid].update({k: v for k, v in dict(row).items() if k != "strategy_id"})
            strategies_by_id[sid]["metrics_source"] = "live"
        elif sid:
            strategies_by_id[sid] = dict(row)
            strategies_by_id[sid]["metrics_source"] = "live"

    trade_rows = await db.query(
        """SELECT strategy_id,
            COUNT(*) FILTER (WHERE side = 'sell' AND pnl IS NOT NULL) as total_trades,
            COUNT(*) as fills_count,
            COUNT(*) FILTER (WHERE side = 'buy') as opens_count,
            COUNT(*) FILTER (WHERE side = 'sell' AND pnl > 0) as wins,
            COUNT(*) FILTER (WHERE side = 'sell' AND pnl < 0) as losses,
            COALESCE(ROUND(AVG(pnl) FILTER (WHERE side = 'sell' AND pnl IS NOT NULL)::numeric, 2), 0) as avg_pnl,
            COALESCE(ROUND(SUM(pnl) FILTER (WHERE side = 'sell' AND pnl IS NOT NULL)::numeric, 2), 0) as total_pnl,
            CASE
                WHEN COUNT(*) FILTER (WHERE side = 'sell' AND pnl IS NOT NULL) = 0 THEN NULL
                ELSE ROUND(
                    COUNT(*) FILTER (WHERE side = 'sell' AND pnl > 0)::numeric
                    / COUNT(*) FILTER (WHERE side = 'sell' AND pnl IS NOT NULL),
                    3
                )
            END as win_rate,
            MAX(created_at) as last_trade_at
        FROM trade_log
        WHERE strategy_id IS NOT NULL
        GROUP BY strategy_id"""
    )
    for row in trade_rows:
        sid = row.get("strategy_id")
        if sid and sid in strategies_by_id:
            strategies_by_id[sid].update({k: v for k, v in dict(row).items() if k != "strategy_id"})
        elif sid:
            strategies_by_id[sid] = dict(row)

    try:
        broker = STATE.get_broker()
        alpaca_positions, sp_rows = await asyncio.gather(
            broker.get_positions(),
            db.query(
                """SELECT strategy_id, symbol, qty, avg_entry_price
                FROM strategy_positions WHERE qty > 0"""
            ),
        )
        price_map = {p.symbol: p.current_price for p in alpaca_positions}
        unrealized_by_strategy: dict[str, float] = {}
        for sp in sp_rows:
            sid = sp.get("strategy_id")
            sym = sp.get("symbol")
            qty = float(sp.get("qty") or 0)
            entry = float(sp.get("avg_entry_price") or 0)
            price = price_map.get(sym, entry)
            unrealized_by_strategy[sid] = unrealized_by_strategy.get(sid, 0) + (price - entry) * qty

        for sid, upnl in unrealized_by_strategy.items():
            if sid in strategies_by_id:
                strategies_by_id[sid]["unrealized_pnl"] = round(upnl, 2)
    except Exception as exc:
        logger.debug("strategy_unrealized_pnl_failed", error=str(exc))

    def _natural_key(s):
        sid = s.get("strategy_id") or ""
        parts = sid.split("_")
        if len(parts) == 2 and parts[1].isdigit():
            return (parts[0], int(parts[1]))
        return (sid, 0)
    result = sorted(strategies_by_id.values(), key=_natural_key)
    return serializers.serialize(result)
