from __future__ import annotations

import asyncio
import json
from pathlib import Path

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.dashboard.helpers import db, serializers
from src.dashboard.state import STATE

logger = structlog.get_logger(__name__)

router = APIRouter()

STRATEGY_CONFIGS_DIR = (Path(__file__).parent.parent.parent.parent / "configs" / "strategies").resolve()


@router.get("/api/portfolio")
async def get_portfolio():
    # / pull live data from alpaca, fall back to trade_log
    try:
        broker = STATE.get_broker()
        balance, positions = await asyncio.gather(
            broker.get_account_balance(),
            broker.get_positions(),
        )
        trades_today = serializers.serialize(await db.query(
            """SELECT * FROM trade_log
            WHERE created_at >= CURRENT_DATE ORDER BY created_at DESC"""
        ))
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
        return {
            "equity": balance.equity,
            "cash": balance.cash,
            "buying_power": balance.buying_power,
            "positions_count": len(positions),
            "daily_pnl": unrealized + realized,
            "positions": [serializers.serialize_position(p) for p in positions],
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
        return {
            "timestamps": timestamps,
            "equity": equity,
            "profit_loss": profit_loss,
            "base_value": data.get("base_value", 100000),
        }
    except Exception as exc:
        logger.debug("equity_history_failed", error=str(exc))
        return {"timestamps": [], "equity": [], "profit_loss": [], "base_value": 100000}


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
    # / build baseline from config files; overlay live + trade rows
    strategies_by_id: dict = {}
    for config_path in sorted(STRATEGY_CONFIGS_DIR.glob("*.json")):
        try:
            cfg = json.loads(config_path.read_text())
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
