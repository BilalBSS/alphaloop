
from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd
import structlog

from src.agents import capital_allocator
from src.agents.capital_allocator import DynamicCaps, get_dynamic_caps
from src.agents.circuit_breaker import CircuitBreakerState
from src.agents.market_tools import (
    fetch_avg_volume,
    fetch_close_history_batch,
    fetch_latest_regime,
    fetch_recent_closes,
    fetch_symbol_beta,
)
from src.agents.position_tools import get_strategy_positions
from src.agents.trade_tools import (
    count_pending_signals_for_strategy,
    count_today_approved_trades,
    count_today_approved_trades_for_strategy,
    fetch_pending_signal_by_id,
    store_approved_trade,
    update_trade_status,
)
from src.brokers.base import BrokerInterface
from src.data.symbols import is_crypto
from src.quant.copula_models import student_t_copula_fit, tail_dependence_coefficient

logger = structlog.get_logger(__name__)


async def _fetch_decision_id(pool, signal_id: int) -> str | None:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT decision_id FROM trade_signals WHERE id = $1", signal_id,
            )
        if row is None:
            return None
        try:
            return row["decision_id"]
        except (KeyError, TypeError):
            return None
    except Exception as exc:
        logger.debug("fetch_decision_id_failed", error=str(exc)[:100])
        return None


def _broadcast_decision_made(
    decision_id: str | None, symbol: str, side: str, qty: int, strategy_id: str | None,
) -> None:
    if not decision_id:
        return
    try:
        from src.agents.data_tools import fire_and_forget
        from src.dashboard.app import _ws_clients, broadcast
    except ImportError:
        return
    if not _ws_clients:
        return
    try:
        fire_and_forget(broadcast("decision_made", {
            "decision_id": decision_id, "symbol": symbol,
            "side": side, "qty": int(qty), "strategy_id": strategy_id,
        }))
    except Exception as exc:
        logger.debug("broadcast_decision_made_failed", error=str(exc)[:120])


def _broadcast_gate_breach(
    decision_id: str | None, signal_id: int, gate: str,
) -> None:
    try:
        from src.agents.data_tools import fire_and_forget
        from src.dashboard.app import _ws_clients, broadcast
    except ImportError:
        return
    if not _ws_clients:
        return
    try:
        fire_and_forget(broadcast("gate_breach", {
            "decision_id": decision_id, "signal_id": signal_id, "gate": gate,
        }))
    except Exception as exc:
        logger.debug("broadcast_gate_breach_failed", error=str(exc)[:120])


class RiskAgent:
    def __init__(
        self,
        max_position_pct: float | None = None,
        max_portfolio_risk: float | None = None,
        tail_dep_threshold: float = 0.30,
        risk_limits: dict | None = None,
    ):
        rl = risk_limits or self._load_risk_limits()
        self._rl = rl
        env_override = os.environ.get("MAX_POSITION_PCT")
        if max_position_pct is None and env_override is not None:
            try:
                max_position_pct = float(env_override)
            except ValueError:
                max_position_pct = None
        self._explicit_max_position_pct = max_position_pct
        self.max_portfolio_risk = (
            max_portfolio_risk
            if max_portfolio_risk is not None
            else float(os.environ.get("MAX_PORTFOLIO_RISK", str(rl.get("max_portfolio_risk", 0.25))))
        )
        self.tail_dep_threshold = tail_dep_threshold
        self._long_only = os.environ.get("LONG_ONLY", "true").lower() in ("true", "1", "yes")
        self._min_cash_reserve_pct = rl.get("min_cash_reserve_pct", 0.10)
        self._max_daily_trades = rl.get("max_daily_trades", 20)
        self._max_daily_trades_per_strategy = rl.get("max_daily_trades_per_strategy", 6)
        self._max_positions_per_strategy = rl.get("max_positions_per_strategy", 4)
        self._activity_scaling_enabled = bool(rl.get("activity_scaling_enabled", True))
        self._caps_cache: tuple[float, DynamicCaps] | None = None
        self._caps_ttl_s = 60.0
        self._max_open_positions = rl.get("max_open_positions", 15)
        self._max_drawdown_hard_stop = rl.get("max_drawdown_hard_stop", -0.20)
        self._consecutive_loss_pause = rl.get("consecutive_loss_pause_count", 3)
        self._consecutive_loss_seconds = rl.get("consecutive_loss_pause_seconds", 3600)
        self._max_liquidity_pct = rl.get("max_liquidity_pct", 0.01)
        self._max_single_trade_loss_pct = float(rl.get("max_single_trade_loss_pct", 0.02))
        self._consecutive_loss_min_pct = float(rl.get("consecutive_loss_min_pct", 0.001))
        self._consecutive_loss_min_abs = float(rl.get("consecutive_loss_min_abs", 10.0))
        self._min_order_notional = float(rl.get("min_order_notional_usd", 1.0))
        self._regime_multipliers = rl.get("regime_sizing_multipliers", {
            "bull": 1.0, "sideways": 0.75, "bear": 0.5, "high_vol": 0.5, "insufficient_data": 0.5,
        })
        self._cb = CircuitBreakerState(
            max_drawdown=self._max_drawdown_hard_stop,
            loss_pause_seconds=int(self._consecutive_loss_seconds),
        )

    @staticmethod
    def _load_risk_limits() -> dict:
        import json
        from pathlib import Path
        path = Path(__file__).parent.parent.parent / "configs" / "risk_limits.json"
        if path.exists():
            return json.loads(path.read_text())
        return {}

    def _caps(self) -> DynamicCaps:
        if self._explicit_max_position_pct is not None:
            slots = max(1, int(self._max_positions_per_strategy))
            per_pos = float(self._explicit_max_position_pct)
            per_strat = float(self._rl.get("max_exposure_per_strategy_pct", 0.30))
            return DynamicCaps(
                active_count=0,
                per_position_pct=per_pos,
                per_strategy_pct=max(per_strat, per_pos * slots),
            )
        now = time.monotonic()
        if self._caps_cache and (now - self._caps_cache[0]) < self._caps_ttl_s:
            return self._caps_cache[1]
        caps = get_dynamic_caps(self._rl)
        self._caps_cache = (now, caps)
        logger.info(
            "dynamic_caps_refreshed",
            active=caps.active_count,
            per_position_pct=caps.per_position_pct,
            per_strategy_pct=caps.per_strategy_pct,
        )
        return caps

    @property
    def max_position_pct(self) -> float:
        return self._caps().per_position_pct

    @property
    def _max_exposure_per_strategy_pct(self) -> float:
        return self._caps().per_strategy_pct

    async def process_signal(
        self, pool, signal_id: int, broker: BrokerInterface,
        strategy_pool=None,
    ) -> dict:
        try:
            return await self._process_signal_inner(pool, signal_id, broker, strategy_pool)
        except Exception as exc:
            logger.error("risk_process_signal_error", signal_id=signal_id, error=str(exc))
            try:
                await update_trade_status(pool, "trade_signals", signal_id, "error")
            except Exception as inner:
                logger.warning(
                    "risk_signal_status_update_failed",
                    signal_id=signal_id, error=str(inner)[:120],
                )
            return {"status": "error", "reason": str(exc)}

    async def _reject(
        self, pool, signal_id: int, label: str, response_reason: str | None = None,
    ) -> dict:
        decision_id = await _fetch_decision_id(pool, signal_id)
        await update_trade_status(pool, "trade_signals", signal_id, "rejected", label)
        _broadcast_gate_breach(decision_id, signal_id, label)
        return {"status": "rejected", "reason": response_reason or label}

    async def _process_signal_inner(
        self, pool, signal_id: int, broker: BrokerInterface,
        strategy_pool=None,
    ) -> dict:
        signal = await fetch_pending_signal_by_id(pool, signal_id)
        if not signal:
            return {"status": "skipped", "reason": "signal_not_found_or_not_pending"}
        symbol = signal["symbol"]
        side = signal["signal_type"]
        strength = max(0.0, min(1.0, float(signal["strength"]) if signal["strength"] else 0.5))

        rej = await self._guard_long_only(pool, signal, signal_id, broker)
        if rej is not None:
            return rej

        # / 3. account state
        balance = await broker.get_account_balance()
        positions = await broker.get_positions()
        if balance.equity <= 0:
            return await self._reject(pool, signal_id, "zero_equity")
        if balance.equity > 0:
            await self._cb.init_from_db(pool)

        # / 4. already-holding short-circuit (buys)
        rej = await self._check_already_holding(pool, signal, signal_id, side, symbol, positions)
        if rej is not None:
            return rej

        # / 5. price quote
        try:
            price = await broker.get_price(symbol)
        except (ConnectionError, TimeoutError, OSError) as exc:
            logger.warning("risk_get_price_failed", symbol=symbol, error=str(exc)[:80])
            return await self._reject(pool, signal_id, "no_price")

        if side == "buy":
            rej = await self._check_circuit_breakers(pool, signal_id, balance)
            if rej is not None:
                return rej
            rej = await self._check_liquidity(pool, signal_id, symbol, balance, strength, price)
            if rej is not None:
                return rej
            rej = await self._check_buy_caps(pool, signal, signal_id, balance, strength, positions)
            if rej is not None:
                return rej
            rej = await self._check_cross_strategy_concentration(pool, signal_id, symbol, balance, price)
            if rej is not None:
                return rej

        qty = await self._compute_size(pool, signal, side, balance, price, strength)

        # / 10-11. sizing modifiers (buys)
        if side == "buy":
            qty = await self._apply_regime_multiplier(pool, symbol, qty)
            qty = await self._apply_beta_adjustment(pool, symbol, qty)

        if qty <= 0:
            return await self._reject(pool, signal_id, "qty_zero")

        # / 12. single-trade loss cap
        rej, qty = await self._apply_single_trade_loss_cap(
            pool, signal, signal_id, side, balance, price, qty, strategy_pool,
        )
        if rej is not None:
            return rej

        # / 13. portfolio exposure cap
        rej, qty = await self._apply_portfolio_cap(
            pool, signal_id, symbol, side, balance, positions, price, qty,
        )
        if rej is not None:
            return rej

        if side == "buy" and len(positions) >= 5:
            qty = await self._apply_tail_dependence_cap(pool, symbol, positions, qty)

        # / 15. approve
        return await self._approve(
            pool, signal_id, signal, symbol, side, qty,
            balance=balance, positions=positions, price=price,
        )


    async def _guard_long_only(
        self, pool, signal: dict, signal_id: int, broker: BrokerInterface,
    ) -> dict | None:
        if not (self._long_only and signal["signal_type"] == "sell"):
            return None
        symbol = signal["symbol"]
        positions_check = await broker.get_positions()
        held = next((p for p in positions_check if p.symbol == symbol), None)
        if not held:
            logger.info("long_only_rejected", symbol=symbol, signal_id=signal_id)
            return await self._reject(pool, signal_id, "long_only_no_position")
        held_qty = held.qty
        signal_qty = float(signal.get("details", {}).get("qty", 0)) if signal.get("details") else 0
        if signal_qty > held_qty:
            signal["details"] = signal.get("details") or {}
            signal["details"]["qty"] = held_qty
            signal["details"]["qty_capped"] = True
            logger.info("sell_qty_capped", symbol=symbol, requested=signal_qty, capped_to=held_qty)
        return None

    async def _check_already_holding(
        self, pool, signal: dict, signal_id: int, side: str, symbol: str, positions: list,
    ) -> dict | None:
        if side != "buy":
            return None
        strategy_id = signal.get("strategy_id")
        if strategy_id:
            strat_positions = await get_strategy_positions(
                pool, strategy_id=strategy_id, symbol=symbol,
            )
            if strat_positions:
                return await self._reject(pool, signal_id, "already_holding")
            return None
        if any(p.symbol == symbol for p in positions):
            return await self._reject(pool, signal_id, "already_holding")
        return None

    async def _check_circuit_breakers(
        self, pool, signal_id: int, balance,
    ) -> dict | None:
        await self._cb.update_peak(pool, balance.equity)
        if self._cb.hard_stop_breached(balance.equity):
            drawdown = self._cb.current_drawdown(balance.equity)
            logger.warning("circuit_breaker_drawdown", drawdown=drawdown, threshold=self._max_drawdown_hard_stop)
            return await self._reject(
                pool, signal_id, "circuit_breaker_drawdown",
                response_reason=f"circuit_breaker_drawdown ({drawdown:.2%})",
            )
        if self._cb.is_paused():
            return await self._reject(
                pool, signal_id, "circuit_breaker_losses",
                response_reason="circuit_breaker_consecutive_losses",
            )
        recent = await fetch_recent_closes(pool, limit=self._consecutive_loss_pause)
        loss_floor = max(
            self._consecutive_loss_min_abs,
            self._consecutive_loss_min_pct * balance.equity,
        )
        if (
            self._is_material_loss_streak(recent, loss_floor, self._consecutive_loss_pause)
            and self._cb.arm_loss_pause(recent[0][1])
        ):
            logger.warning(
                "circuit_breaker_consecutive_losses",
                count=len(recent), loss_floor=round(loss_floor, 2),
            )
            self._alert_pause(pool, len(recent), loss_floor)
            return await self._reject(
                pool, signal_id, "circuit_breaker_losses",
                response_reason="circuit_breaker_consecutive_losses",
            )
        return None

    @staticmethod
    def _is_material_loss_streak(
        recent: list[tuple[float, float]], loss_floor: float, count: int,
    ) -> bool:
        # / ignore sub-floor scratches
        return len(recent) >= count and all(pnl < -loss_floor for pnl, _ in recent)

    def _alert_pause(self, pool, count: int, loss_floor: float) -> None:
        # / surface breaker pause
        try:
            from src.agents.data_tools import fire_and_forget, log_event
            from src.notifications.notifier import notify_system_error
            fire_and_forget(log_event(
                pool, "warning", "risk", "circuit_breaker_paused",
                details={
                    "consecutive_losses": count,
                    "loss_floor": round(loss_floor, 2),
                    "pause_seconds": int(self._consecutive_loss_seconds),
                },
            ))
            notify_system_error(
                f"circuit breaker paused: {count} losses over ${loss_floor:.0f}",
                "circuit_breaker",
            )
        except Exception as exc:
            logger.debug("breaker_alert_failed", error=str(exc)[:100])

    async def _check_liquidity(
        self, pool, signal_id: int, symbol: str, balance, strength: float, price: float,
    ) -> dict | None:
        avg_vol = await fetch_avg_volume(pool, symbol)
        if not (avg_vol and avg_vol > 0 and price > 0):
            return None
        trade_value = balance.equity * self.max_position_pct * strength
        daily_dollar_vol = avg_vol * price
        if trade_value > daily_dollar_vol * self._max_liquidity_pct:
            return await self._reject(pool, signal_id, "insufficient_liquidity")
        return None

    async def _check_buy_caps(
        self, pool, signal: dict, signal_id: int, balance, strength: float, positions: list,
    ) -> dict | None:
        if balance.cash < balance.equity * self._min_cash_reserve_pct:
            return await self._reject(pool, signal_id, "cash_reserve_insufficient")
        if len(positions) >= self._max_open_positions:
            return await self._reject(
                pool, signal_id, "max_positions",
                response_reason=f"max_positions ({self._max_open_positions}) reached",
            )
        today_count = await count_today_approved_trades(pool)
        if today_count >= self._max_daily_trades:
            return await self._reject(
                pool, signal_id, "max_daily_trades",
                response_reason=f"max_daily_trades ({self._max_daily_trades}) reached",
            )
        return await self._check_strategy_breadth(pool, signal, signal_id, balance, strength)

    async def _check_strategy_breadth(
        self, pool, signal: dict, signal_id: int, balance, strength: float,
    ) -> dict | None:
        signal_strategy_id = signal.get("strategy_id") or ""
        if not signal_strategy_id:
            return None
        strat_today = await count_today_approved_trades_for_strategy(pool, signal_strategy_id)
        if strat_today >= self._max_daily_trades_per_strategy:
            return await self._reject(
                pool, signal_id, "strategy_daily_cap",
                response_reason=(
                    f"strategy_daily_cap ({self._max_daily_trades_per_strategy}) "
                    f"reached for {signal_strategy_id}"
                ),
            )
        strat_positions = await get_strategy_positions(pool, strategy_id=signal_strategy_id)
        open_slots = sum(1 for sp in strat_positions if float(sp.get("qty") or 0) > 0)
        if open_slots >= self._max_positions_per_strategy:
            return await self._reject(
                pool, signal_id, "strategy_slot_cap",
                response_reason=(
                    f"strategy_slot_cap ({self._max_positions_per_strategy}) "
                    f"reached for {signal_strategy_id}"
                ),
            )
        strat_notional = sum(
            float(sp.get("qty") or 0) * float(sp.get("avg_entry_price") or 0)
            for sp in strat_positions
        )
        if balance.equity > 0:
            strat_exposure = strat_notional / balance.equity
            projected_notional = strat_notional + balance.equity * self.max_position_pct * strength
            projected_exposure = projected_notional / balance.equity
            if projected_exposure > self._max_exposure_per_strategy_pct:
                return await self._reject(
                    pool, signal_id, "strategy_exposure_cap",
                    response_reason=(
                        f"strategy_exposure_cap ({self._max_exposure_per_strategy_pct:.1%}) "
                        f"would be exceeded for {signal_strategy_id} (current {strat_exposure:.1%})"
                    ),
                )
        return None

    async def _check_cross_strategy_concentration(
        self, pool, signal_id: int, symbol: str, balance, price: float,
    ) -> dict | None:
        all_sym_positions = await get_strategy_positions(pool, symbol=symbol)
        if not all_sym_positions:
            return None
        total_held_value = sum(sp["qty"] for sp in all_sym_positions) * price
        max_concentration = self.max_position_pct * 2 * balance.equity
        if total_held_value >= max_concentration:
            return await self._reject(
                pool, signal_id, "cross_strategy_concentration",
                response_reason=f"cross_strategy_concentration: {symbol}",
            )
        return None

    # / -- sizing methods --

    async def _compute_size(
        self, pool, signal: dict, side: str, balance, price: float, strength: float,
    ) -> int:
        if side == "sell":
            return await self._resolve_sell_qty(pool, signal)
        return await self._compute_buy_size(pool, signal, balance, price, strength)

    async def _resolve_sell_qty(self, pool, signal: dict) -> int:
        signal_details = signal.get("details") or {}
        qty = int(float(signal_details.get("qty", 0)))
        if qty > 0:
            return qty
        strat_pos = await get_strategy_positions(
            pool, strategy_id=signal.get("strategy_id"), symbol=signal["symbol"],
        )
        return int(strat_pos[0]["qty"]) if strat_pos else 0

    async def _compute_buy_size(
        self, pool, signal: dict, balance, price: float, strength: float,
    ) -> float:
        # / kelly-weighted x activity-scaled
        strategy_id = signal.get("strategy_id") or ""
        max_pct = await capital_allocator.get_allocation(
            pool, strategy_id, max_position_pct_default=self.max_position_pct,
        )
        activity_scale = await self._get_activity_scale(pool, strategy_id)
        qty_raw = (balance.equity * max_pct * strength * activity_scale) / price
        return self._size_with_floor(signal["symbol"], qty_raw, price, balance.equity)

    def _round_shares(self, symbol: str, qty: float) -> float:
        # / crypto fractional, equity whole
        if qty <= 0:
            return 0.0
        if is_crypto(symbol):
            return round(float(qty), 6)
        return int(qty)

    def _size_with_floor(
        self, symbol: str, qty_raw: float, price: float, equity: float,
    ) -> float:
        # / don't zero affordable buys
        if qty_raw <= 0:
            return 0.0
        if is_crypto(symbol):
            if qty_raw * price < self._min_order_notional:
                return 0.0
            return round(qty_raw, 6)
        shares = int(qty_raw)
        if shares == 0 and price <= equity * self.max_position_pct:
            return 1
        return shares

    async def _get_activity_scale(self, pool, strategy_id: str) -> float:
        if not (self._activity_scaling_enabled and strategy_id):
            return 1.0
        try:
            pending_n = await count_pending_signals_for_strategy(pool, strategy_id)
            if pending_n > 1:
                scale = 1.0 / (pending_n ** 0.5)
                logger.debug(
                    "activity_scaled", strategy_id=strategy_id,
                    pending=pending_n, scale=round(scale, 3),
                )
                return scale
        except Exception as exc:
            logger.debug(
                "activity_scale_skipped", strategy_id=strategy_id, error=str(exc)[:80],
            )
        return 1.0

    async def _apply_regime_multiplier(self, pool, symbol: str, qty: float) -> float:
        try:
            regime_label = await fetch_latest_regime(pool) or "insufficient_data"
            regime_mult = self._regime_multipliers.get(regime_label, 0.5)
            return self._round_shares(symbol, qty * regime_mult)
        except Exception as exc:
            logger.debug("regime_sizing_skipped", symbol=symbol, error=str(exc)[:100])
            return qty

    async def _apply_beta_adjustment(self, pool, symbol: str, qty: float) -> float:
        try:
            beta = await fetch_symbol_beta(pool, symbol)
            if beta is not None and beta > 1.5:
                new_qty = self._round_shares(symbol, qty / beta)
                if not is_crypto(symbol):
                    new_qty = max(1, new_qty)
                logger.info("beta_adjusted", symbol=symbol, beta=beta, qty=new_qty)
                return new_qty
        except Exception as exc:
            logger.debug("beta_sizing_skipped", symbol=symbol, error=str(exc)[:100])
        return qty

    async def _apply_single_trade_loss_cap(
        self, pool, signal: dict, signal_id: int, side: str, balance,
        price: float, qty: float, strategy_pool,
    ) -> tuple[dict | None, float]:
        if side != "buy" or strategy_pool is None or balance.equity <= 0:
            return None, qty
        stop_distance = self._infer_stop_distance(strategy_pool, signal.get("strategy_id"))
        if not (stop_distance and stop_distance > 0):
            return None, qty
        max_loss_value = balance.equity * self._max_single_trade_loss_pct
        max_qty_by_loss = self._round_shares(
            signal["symbol"], max_loss_value / (price * stop_distance),
        )
        if max_qty_by_loss >= qty:
            return None, qty
        logger.info(
            "single_trade_loss_cap",
            symbol=signal["symbol"], stop_distance=round(stop_distance, 4),
            original_qty=qty, capped_qty=max_qty_by_loss,
        )
        if max_qty_by_loss <= 0:
            return await self._reject(pool, signal_id, "single_trade_loss_cap"), 0
        return None, max_qty_by_loss

    async def _apply_portfolio_cap(
        self, pool, signal_id: int, symbol: str, side: str, balance, positions: list,
        price: float, qty: float,
    ) -> tuple[dict | None, float]:
        if side != "buy":
            return None, qty
        total_position_value = sum(p.market_value for p in positions)
        new_position_value = qty * price
        total_exposure = (total_position_value + new_position_value) / max(balance.equity, 1)
        if total_exposure <= self.max_portfolio_risk:
            return None, qty
        # / scale down to fit
        available = (self.max_portfolio_risk * balance.equity) - total_position_value
        if available <= 0:
            return await self._reject(pool, signal_id, "portfolio_risk_exceeded"), 0
        new_qty = self._round_shares(symbol, available / price)
        if new_qty <= 0:
            return await self._reject(pool, signal_id, "portfolio_risk_exceeded"), 0
        return None, new_qty

    async def _apply_tail_dependence_cap(
        self, pool, symbol: str, positions: list, qty: float,
    ) -> float:
        try:
            tail_dep = await self._check_tail_dependence(pool, symbol, positions)
            if tail_dep is not None and tail_dep > self.tail_dep_threshold:
                new_qty = self._round_shares(symbol, qty / 2)
                if not is_crypto(symbol):
                    new_qty = max(1, new_qty)
                logger.warning(
                    "tail_dependence_sizing_down",
                    symbol=symbol, tail_dep=tail_dep, new_qty=new_qty,
                )
                return new_qty
        except Exception as exc:
            logger.warning("copula_check_failed", symbol=symbol, error=str(exc))
        return qty

    async def _approve(
        self, pool, signal_id: int, signal: dict, symbol: str, side: str, qty: int,
        *, balance=None, positions=None, price: float | None = None,
    ) -> dict:
        strategy_id = signal.get("strategy_id")
        decision_id = signal.get("decision_id")
        sizing_details = await self._build_sizing_details(
            pool, signal, side, qty,
            balance=balance, positions=positions, price=price,
        )
        trade_id = await store_approved_trade(
            pool, signal_id=signal_id, symbol=symbol, side=side,
            qty=float(qty), order_type="market", strategy_id=strategy_id,
            decision_id=decision_id, sizing_details=sizing_details,
        )
        await update_trade_status(pool, "trade_signals", signal_id, "processed")
        _broadcast_decision_made(decision_id, symbol, side, qty, strategy_id)
        logger.info(
            "trade_approved",
            signal_id=signal_id, trade_id=trade_id,
            symbol=symbol, qty=qty, side=side,
        )
        return {
            "status": "approved",
            "trade_id": trade_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "decision_id": decision_id,
        }

    async def _build_sizing_details(
        self, pool, signal: dict, side: str, qty: int,
        *, balance=None, positions=None, price: float | None = None,
    ) -> dict:
        # / sizing rationale snapshot
        try:
            strategy_id = signal.get("strategy_id") or ""
            try:
                kelly_fraction = await capital_allocator.get_allocation(
                    pool, strategy_id, max_position_pct_default=self.max_position_pct,
                )
            except Exception:
                kelly_fraction = float(self.max_position_pct)
            try:
                regime_label = await fetch_latest_regime(pool) or "insufficient_data"
            except Exception:
                regime_label = signal.get("regime") or "unknown"
            if not isinstance(regime_label, str):
                regime_label = "unknown"
            regime_mult = self._regime_multipliers.get(regime_label, 1.0)
            gates = await self._build_gate_trace(
                pool, signal, qty,
                balance=balance, positions=positions, price=price,
            )
            return {
                "strength": float(signal.get("strength") or 0),
                "kelly_fraction": float(kelly_fraction),
                "regime": regime_label,
                "regime_multiplier": float(regime_mult),
                "side": side,
                "final_qty": int(qty),
                "gates": gates,
            }
        except Exception as exc:
            logger.debug("sizing_details_build_failed", error=str(exc)[:100])
            return {"side": side, "final_qty": int(qty)}

    async def _build_gate_trace(
        self, pool, signal: dict, qty: int,
        *, balance=None, positions=None, price: float | None = None,
    ) -> list[dict]:
        # / 8-gate snapshot at approve
        if balance is None or positions is None or price is None:
            return []
        nav = float(balance.equity)
        symbol = signal["symbol"]
        strategy_id = signal.get("strategy_id") or ""

        gates: list[dict] = [
            {
                "name": "position_count",
                "value": len(positions),
                "limit": self._max_open_positions,
                "status": "pass",
            },
        ]

        try:
            strat_positions = await get_strategy_positions(pool, strategy_id=strategy_id) if strategy_id else []
            strat_notional = sum(
                float(sp.get("qty") or 0) * float(sp.get("avg_entry_price") or 0)
                for sp in strat_positions
            )
            strat_pct = strat_notional / nav if nav > 0 else 0.0
            gates.append({
                "name": "strategy_exposure",
                "value": round(strat_pct, 4),
                "limit": float(self._max_exposure_per_strategy_pct),
                "status": "pass",
            })
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug("gate_strategy_exposure_failed", error=str(exc)[:80])
            gates.append({"name": "strategy_exposure", "value": None, "limit": None, "status": "pass"})

        gates.append({"name": "sector_exposure", "value": None, "limit": None, "status": "pass"})

        try:
            sym_positions = await get_strategy_positions(pool, symbol=symbol)
            held_value = sum(float(sp.get("qty") or 0) for sp in sym_positions) * float(price)
            cluster_pct = held_value / nav if nav > 0 else 0.0
            limit_pct = float(self.max_position_pct) * 2
            gates.append({
                "name": "correlation_cluster",
                "value": round(cluster_pct, 4),
                "limit": round(limit_pct, 4),
                "status": "pass",
            })
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug("gate_correlation_cluster_failed", error=str(exc)[:80])
            gates.append({"name": "correlation_cluster", "value": None, "limit": None, "status": "pass"})

        try:
            lam = await self._check_tail_dependence(pool, symbol, positions) if len(positions) >= 5 else None
            gates.append({
                "name": "tail_dependence",
                "value": round(float(lam), 4) if lam is not None else None,
                "limit": float(self.tail_dep_threshold),
                "status": "pass",
            })
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug("gate_tail_dependence_failed", error=str(exc)[:80])
            gates.append({"name": "tail_dependence", "value": None, "limit": float(self.tail_dep_threshold), "status": "pass"})

        gates.append({"name": "var_95", "value": None, "limit": None, "status": "pass"})

        dd = self._cb.current_drawdown(nav)
        gates.append({
            "name": "drawdown_kill",
            "value": round(float(dd), 4),
            "limit": -float(self._max_drawdown_hard_stop),
            "status": "pass",
        })

        trade_value = float(qty) * float(price)
        liq_pct = trade_value / nav if nav > 0 else 0.0
        gates.append({
            "name": "min_liquidity",
            "value": round(liq_pct, 4),
            "limit": float(self._max_liquidity_pct),
            "status": "pass",
        })

        return gates

    def _infer_stop_distance(self, strategy_pool, strategy_id) -> float | None:
        if not strategy_id:
            return None
        try:
            entry = strategy_pool.get(strategy_id)
            if not entry:
                return None
            cfg = entry.strategy.config
            sl = cfg.get("exit_conditions", {}).get("stop_loss", {}) or {}
            t = (sl.get("type") or "fixed_pct").lower()
            if t in ("fixed_pct", "percent", "pct"):
                pct = sl.get("pct")
                return float(pct) if pct else None
            if "atr" in t or t in ("trailing", "chandelier"):
                return 0.02
        except Exception as exc:
            logger.debug("stop_distance_infer_failed", strategy_id=strategy_id, error=str(exc)[:100])
        return None

    async def _check_tail_dependence(
        self, pool, symbol: str, positions: list,
    ) -> float | None:
        position_symbols = [p.symbol for p in positions] + [symbol]
        rows = await fetch_close_history_batch(
            pool, position_symbols, bars_per_symbol=252,
        )
        if not rows:
            return None
        df = pd.DataFrame([dict(r) for r in rows])
        if len(df) == 0:
            return None
        pivot = df.pivot_table(index="date", columns="symbol", values="close")
        if pivot.shape[0] < 10 or pivot.shape[1] < 2:
            return None
        returns = pivot.pct_change().dropna()
        if returns.shape[0] < 10:
            return None

        from scipy.stats import rankdata
        u_data = np.column_stack([
            rankdata(returns.iloc[:, j]) / (returns.shape[0] + 1)
            for j in range(returns.shape[1])
        ])
        nu, corr = student_t_copula_fit(u_data)
        td = tail_dependence_coefficient("student_t", (nu, corr))
        return td.get("lambda_lower", 0.0)
