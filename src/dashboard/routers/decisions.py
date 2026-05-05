from __future__ import annotations

import json

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.dashboard.helpers import db, serializers

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/api/decisions")
async def list_decisions(limit: int = 50):
    limit = max(1, min(limit, 200))
    rows = await db.query(
        """SELECT s.decision_id, s.id AS signal_id, s.strategy_id, s.symbol,
                  s.signal_type, s.strength, s.regime, s.status,
                  s.rejection_reason, s.created_at,
                  a.id AS approved_id, a.qty, a.order_type,
                  a.status AS approved_status,
                  t.id AS log_id, t.price, t.pnl
        FROM trade_signals s
        LEFT JOIN approved_trades a ON a.decision_id = s.decision_id
        LEFT JOIN trade_log t ON t.decision_id = s.decision_id
        WHERE s.decision_id IS NOT NULL
        ORDER BY s.created_at DESC LIMIT $1""",
        limit,
    )
    return {"decisions": serializers.serialize(rows)}


@router.get("/api/decisions/{decision_id}")
async def get_decision_chain(decision_id: str):
    signal = await db.query_one(
        "SELECT * FROM trade_signals WHERE decision_id = $1", decision_id,
    )
    if not signal:
        return JSONResponse({"error": "decision not found"}, status_code=404)
    approved = await db.query_one(
        "SELECT * FROM approved_trades WHERE decision_id = $1", decision_id,
    )
    fill = await db.query_one(
        "SELECT * FROM trade_log WHERE decision_id = $1", decision_id,
    )
    analysis = await db.query_one(
        """SELECT date, composite_score, fundamental_score, technical_score,
                  regime, regime_confidence, details
        FROM analysis_scores
        WHERE symbol = $1 AND date <= $2::date
        ORDER BY date DESC LIMIT 1""",
        signal["symbol"], signal.get("created_at"),
    )
    gates = _gate_trace(signal, approved)
    return {
        "decision_id": decision_id,
        "signal": serializers.serialize_one(signal),
        "approved": serializers.serialize_one(approved) if approved else None,
        "fill": serializers.serialize_one(fill) if fill else None,
        "analysis": serializers.serialize_one(analysis) if analysis else None,
        "gates": gates,
    }


_GATE_NAMES = (
    "position_count", "strategy_exposure", "sector_exposure",
    "correlation_cluster", "tail_dependence", "var_95",
    "drawdown_kill", "min_liquidity",
)


def _persisted_gates(approved: dict | None) -> list[dict] | None:
    if not approved:
        return None
    sizing = approved.get("sizing_details")
    if isinstance(sizing, str):
        try:
            sizing = json.loads(sizing)
        except (TypeError, ValueError):
            return None
    if not isinstance(sizing, dict):
        return None
    gates = sizing.get("gates")
    if isinstance(gates, list) and gates:
        return gates
    return None


def _gate_trace(signal: dict, approved: dict | None) -> list[dict]:
    persisted = _persisted_gates(approved)
    if persisted is not None:
        return persisted
    # / fallback for legacy decisions
    rejected = signal.get("status") == "rejected"
    reason = signal.get("rejection_reason") or ""
    gates = []
    for name in _GATE_NAMES:
        if rejected and name in reason:
            status = "fail"
        elif approved is not None:
            status = "pass"
        else:
            status = "pending"
        gates.append({"name": name, "status": status})
    return gates
