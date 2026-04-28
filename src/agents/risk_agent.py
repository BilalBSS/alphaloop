# / risk agent — evaluates trade signals for portfolio risk before approving
# / uses copula-based tail dependence for correlation risk
# / skips copula on small portfolios (< 5 positions or < 10 days history)

from __future__ import annotations

import os
from typing import Any

import numpy as np
import structlog

from src.agents import capital_allocator, tools
from src.agents.circuit_breaker import CircuitBreakerState
from src.brokers.base import BrokerInterface

logger = structlog.get_logger(__name__)


class RiskAgent:
    def __init__(
        self,
        max_position_pct: float | None = None,
        max_portfolio_risk: float | None = None,
        tail_dep_threshold: float = 0.30,
        risk_limits: dict | None = None,
    ):
        rl = risk_limits or self._load_risk_limits()
        self.max_position_pct = (
            max_position_pct
            if max_position_pct is not None
            else float(os.environ.get("MAX_POSITION_PCT", str(rl.get("max_position_pct", 0.08))))
        )
        self.max_portfolio_risk = (
            max_portfolio_risk
            if max_portfolio_risk is not None
            else float(os.environ.get("MAX_PORTFOLIO_RISK", str(rl.get("max_portfolio_risk", 0.25))))
        )
        self.tail_dep_threshold = tail_dep_threshold
        self._long_only = os.environ.get("LONG_ONLY", "true").lower() in ("true", "1", "yes")
        self._min_cash_reserve_pct = rl.get("min_cash_reserve_pct", 0.10)
        self._max_daily_trades = rl.get("max_daily_trades", 20)
        # / per-strategy breadth: 3 independent guards (slot count, NAV, activity scaling)
        self._max_daily_trades_per_strategy = rl.get("max_daily_trades_per_strategy", 6)
        self._max_positions_per_strategy = rl.get("max_positions_per_strategy", 4)
        self._max_exposure_per_strategy_pct = float(rl.get("max_exposure_per_strategy_pct", 0.08))
        self._activity_scaling_enabled = bool(rl.get("activity_scaling_enabled", True))
        self._max_open_positions = rl.get("max_open_positions", 15)
        self._max_drawdown_hard_stop = rl.get("max_drawdown_hard_stop", -0.20)
        self._consecutive_loss_pause = rl.get("consecutive_loss_pause_count", 3)
        self._consecutive_loss_seconds = rl.get("consecutive_loss_pause_seconds", 3600)
        self._max_liquidity_pct = rl.get("max_liquidity_pct", 0.01)
        self._max_single_trade_loss_pct = float(rl.get("max_single_trade_loss_pct", 0.02))
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

    async def process_signal(
        self, pool, signal_id: int, broker: BrokerInterface,
        strategy_pool=None,
    ) -> dict:
        # / evaluate one trade signal, approve or reject
        try:
            return await self._process_signal_inner(pool, signal_id, broker, strategy_pool)
        except Exception as exc:
            logger.error("risk_process_signal_error", signal_id=signal_id, error=str(exc))
            try:
                await tools.update_trade_status(pool, "trade_signals", signal_id, "error")
            except Exception as inner:
                logger.warning(
                    "risk_signal_status_update_failed",
                    signal_id=signal_id, error=str(inner)[:120],
                )
            return {"status": "error", "reason": str(exc)}

    async def _reject(
        self, pool, signal_id: int, label: str, response_reason: str | None = None,
    ) -> dict:
        # / write rejected status + emit standard response
        await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected", label)
        return {"status": "rejected", "reason": response_reason or label}

    async def _process_signal_inner(
        self, pool, signal_id: int, broker: BrokerInterface,
        strategy_pool=None,
    ) -> dict:
        # / 1. fetch + normalize signal
        signal = await tools.fetch_pending_signal_by_id(pool, signal_id)
        if not signal:
            return {"status": "skipped", "reason": "signal_not_found_or_not_pending"}
        symbol = signal["symbol"]
        side = signal["signal_type"]
        strength = max(0.0, min(1.0, float(signal["strength"]) if signal["strength"] else 0.5))

        # / 2. long-only guard (sells); may cap qty in signal
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
        except Exception:
            return await self._reject(pool, signal_id, "no_price")

        # / 6-8. buy-only gates: circuit breaker -> liquidity -> caps -> concentration
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

        # / 9. compute base position size
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
            pool, signal_id, side, balance, positions, price, qty,
        )
        if rej is not None:
            return rej

        # / 14. copula tail dependence (optional)
        if side == "buy" and len(positions) >= 5:
            qty = await self._apply_tail_dependence_cap(pool, symbol, positions, qty)

        # / 15. approve
        return await self._approve(pool, signal_id, signal, symbol, side, qty)

    # / -- gate methods (each returns rejection dict or None to continue) --

    async def _guard_long_only(
        self, pool, signal: dict, signal_id: int, broker: BrokerInterface,
    ) -> dict | None:
        # / sells only when we hold the symbol; cap qty to held quantity
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
        # / strategies can hold the same symbol independently; reject duplicate-buy from same strategy
        if side != "buy":
            return None
        strategy_id = signal.get("strategy_id")
        if strategy_id:
            strat_positions = await tools.get_strategy_positions(
                pool, strategy_id=strategy_id, symbol=symbol,
            )
            if strat_positions:
                return await self._reject(pool, signal_id, "already_holding")
            return None
        # / fallback when signal has no strategy_id
        if any(p.symbol == symbol for p in positions):
            return await self._reject(pool, signal_id, "already_holding")
        return None

    async def _check_circuit_breakers(
        self, pool, signal_id: int, balance,
    ) -> dict | None:
        # / drawdown + consecutive-loss circuit breakers (buy-side only)
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
        recent_pnl = await tools.fetch_recent_pnl(pool, limit=self._consecutive_loss_pause)
        if len(recent_pnl) >= self._consecutive_loss_pause and all(p < 0 for p in recent_pnl):
            self._cb.record_loss_pause()
            logger.warning("circuit_breaker_consecutive_losses", count=len(recent_pnl))
            return await self._reject(
                pool, signal_id, "circuit_breaker_losses",
                response_reason="circuit_breaker_consecutive_losses",
            )
        return None

    async def _check_liquidity(
        self, pool, signal_id: int, symbol: str, balance, strength: float, price: float,
    ) -> dict | None:
        # / cap trade value to a fraction of daily dollar volume
        avg_vol = await tools.fetch_avg_volume(pool, symbol)
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
        # / global caps: cash reserve, open positions, daily trades + per-strategy breadth
        if balance.cash < balance.equity * self._min_cash_reserve_pct:
            return await self._reject(pool, signal_id, "cash_reserve_insufficient")
        if len(positions) >= self._max_open_positions:
            return await self._reject(
                pool, signal_id, "max_positions",
                response_reason=f"max_positions ({self._max_open_positions}) reached",
            )
        today_count = await tools.count_today_approved_trades(pool)
        if today_count >= self._max_daily_trades:
            return await self._reject(
                pool, signal_id, "max_daily_trades",
                response_reason=f"max_daily_trades ({self._max_daily_trades}) reached",
            )
        # / per-strategy breadth: daily count, slot count, NAV exposure
        return await self._check_strategy_breadth(pool, signal, signal_id, balance, strength)

    async def _check_strategy_breadth(
        self, pool, signal: dict, signal_id: int, balance, strength: float,
    ) -> dict | None:
        signal_strategy_id = signal.get("strategy_id") or ""
        if not signal_strategy_id:
            return None
        strat_today = await tools.count_today_approved_trades_for_strategy(pool, signal_strategy_id)
        if strat_today >= self._max_daily_trades_per_strategy:
            return await self._reject(
                pool, signal_id, "strategy_daily_cap",
                response_reason=(
                    f"strategy_daily_cap ({self._max_daily_trades_per_strategy}) "
                    f"reached for {signal_strategy_id}"
                ),
            )
        strat_positions = await tools.get_strategy_positions(pool, strategy_id=signal_strategy_id)
        open_slots = sum(1 for sp in strat_positions if float(sp.get("qty") or 0) > 0)
        if open_slots >= self._max_positions_per_strategy:
            return await self._reject(
                pool, signal_id, "strategy_slot_cap",
                response_reason=(
                    f"strategy_slot_cap ({self._max_positions_per_strategy}) "
                    f"reached for {signal_strategy_id}"
                ),
            )
        # / NAV exposure pre-check: would this trade push strategy past cap?
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
        # / cap aggregate symbol holding at 2x single-strategy max
        all_sym_positions = await tools.get_strategy_positions(pool, symbol=symbol)
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
        # / sells use held qty; buys use kelly + activity scaling
        if side == "sell":
            return await self._resolve_sell_qty(pool, signal)
        return await self._compute_buy_size(pool, signal, balance, price, strength)

    async def _resolve_sell_qty(self, pool, signal: dict) -> int:
        # / use signal.qty when present, else fall back to strategy's full position
        signal_details = signal.get("details") or {}
        qty = int(float(signal_details.get("qty", 0)))
        if qty > 0:
            return qty
        strat_pos = await tools.get_strategy_positions(
            pool, strategy_id=signal.get("strategy_id"), symbol=signal["symbol"],
        )
        return int(strat_pos[0]["qty"]) if strat_pos else 0

    async def _compute_buy_size(
        self, pool, signal: dict, balance, price: float, strength: float,
    ) -> int:
        # / kelly-weighted sizing + López de Prado activity scaling
        strategy_id = signal.get("strategy_id") or ""
        max_pct = await capital_allocator.get_allocation(
            pool, strategy_id, max_position_pct_default=self.max_position_pct,
        )
        activity_scale = 1.0
        if self._activity_scaling_enabled and strategy_id:
            try:
                pending_n = await tools.count_pending_signals_for_strategy(pool, strategy_id)
                if pending_n > 1:
                    activity_scale = 1.0 / (pending_n ** 0.5)
                    logger.debug(
                        "activity_scaled", strategy_id=strategy_id,
                        pending=pending_n, scale=round(activity_scale, 3),
                    )
            except Exception as exc:
                logger.debug(
                    "activity_scale_skipped", strategy_id=strategy_id, error=str(exc)[:80],
                )
        qty_raw = (balance.equity * max_pct * strength * activity_scale) / price
        return max(0, int(qty_raw))

    async def _apply_regime_multiplier(self, pool, symbol: str, qty: int) -> int:
        try:
            regime_label = await tools.fetch_latest_regime(pool) or "insufficient_data"
            regime_mult = self._regime_multipliers.get(regime_label, 0.5)
            return int(qty * regime_mult)
        except Exception as exc:
            # / regime lookup failed — proceed at full size
            logger.debug("regime_sizing_skipped", symbol=symbol, error=str(exc)[:100])
            return qty

    async def _apply_beta_adjustment(self, pool, symbol: str, qty: int) -> int:
        try:
            beta = await tools.fetch_symbol_beta(pool, symbol)
            if beta is not None and beta > 1.5:
                new_qty = max(1, int(qty * (1.0 / beta)))
                logger.info("beta_adjusted", symbol=symbol, beta=beta, qty=new_qty)
                return new_qty
        except Exception as exc:
            logger.debug("beta_sizing_skipped", symbol=symbol, error=str(exc)[:100])
        return qty

    async def _apply_single_trade_loss_cap(
        self, pool, signal: dict, signal_id: int, side: str, balance,
        price: float, qty: int, strategy_pool,
    ) -> tuple[dict | None, int]:
        # / size down so worst-case stop loss <= max_single_trade_loss_pct of equity
        if side != "buy" or strategy_pool is None or balance.equity <= 0:
            return None, qty
        stop_distance = self._infer_stop_distance(strategy_pool, signal.get("strategy_id"))
        if not (stop_distance and stop_distance > 0):
            return None, qty
        max_loss_value = balance.equity * self._max_single_trade_loss_pct
        max_qty_by_loss = int(max_loss_value / (price * stop_distance))
        if max_qty_by_loss >= qty:
            return None, qty
        logger.info(
            "single_trade_loss_cap",
            symbol=signal["symbol"], stop_distance=round(stop_distance, 4),
            original_qty=qty, capped_qty=max_qty_by_loss,
        )
        new_qty = max(0, max_qty_by_loss)
        if new_qty <= 0:
            return await self._reject(pool, signal_id, "single_trade_loss_cap"), 0
        return None, new_qty

    async def _apply_portfolio_cap(
        self, pool, signal_id: int, side: str, balance, positions: list,
        price: float, qty: int,
    ) -> tuple[dict | None, int]:
        # / hard cap on aggregate exposure (buys only)
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
        new_qty = max(0, int(available / price))
        if new_qty <= 0:
            return await self._reject(pool, signal_id, "portfolio_risk_exceeded"), 0
        return None, new_qty

    async def _apply_tail_dependence_cap(
        self, pool, symbol: str, positions: list, qty: int,
    ) -> int:
        try:
            tail_dep = await self._check_tail_dependence(pool, symbol, positions)
            if tail_dep is not None and tail_dep > self.tail_dep_threshold:
                new_qty = max(1, qty // 2)
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
    ) -> dict:
        strategy_id = signal.get("strategy_id")
        trade_id = await tools.store_approved_trade(
            pool, signal_id=signal_id, symbol=symbol, side=side,
            qty=float(qty), order_type="market", strategy_id=strategy_id,
        )
        await tools.update_trade_status(pool, "trade_signals", signal_id, "processed")
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
        }

    def _infer_stop_distance(self, strategy_pool, strategy_id) -> float | None:
        # / stop distance as a fraction of entry price
        # / fixed_pct -> pct directly; atr-based -> conservative 2% default
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
        # / fit t-copula to portfolio returns; returns lambda_lower or None
        from src.quant.copula_models import student_t_copula_fit, tail_dependence_coefficient

        position_symbols = [p.symbol for p in positions] + [symbol]
        rows = await tools.fetch_close_history_batch(
            pool, position_symbols, bars_per_symbol=252,
        )
        if not rows:
            return None

        import pandas as pd
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
