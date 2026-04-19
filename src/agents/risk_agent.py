# / risk agent — evaluates trade signals for portfolio risk before approving
# / uses copula-based tail dependence for correlation risk
# / skips copula on small portfolios (< 5 positions or < 10 days history)

from __future__ import annotations

import os

import time

import numpy as np
import structlog

from src.agents import tools
from src.agents import capital_allocator
from src.brokers.base import BrokerInterface
from src.data.symbols import get_sector

logger = structlog.get_logger(__name__)

# / circuit breaker state (module-level)
_peak_equity: float = 0.0
_circuit_breaker_until: float = 0.0


async def _init_peak_equity(pool) -> None:
    # / restore peak equity from db on startup
    global _peak_equity
    restored = await tools.fetch_peak_equity(pool)
    if restored > 0:
        _peak_equity = restored
        logger.info("peak_equity_restored", peak=restored)


class RiskAgent:
    def __init__(
        self,
        max_position_pct: float | None = None,
        max_portfolio_risk: float | None = None,
        tail_dep_threshold: float = 0.30,
        risk_limits: dict | None = None,
    ):
        rl = risk_limits or self._load_risk_limits()
        self.max_position_pct = max_position_pct or float(
            os.environ.get("MAX_POSITION_PCT", str(rl.get("max_position_pct", 0.08)))
        )
        self.max_portfolio_risk = max_portfolio_risk or float(
            os.environ.get("MAX_PORTFOLIO_RISK", str(rl.get("max_portfolio_risk", 0.25)))
        )
        self.tail_dep_threshold = tail_dep_threshold
        self._min_cash_reserve_pct = rl.get("min_cash_reserve_pct", 0.10)
        self._max_daily_trades = rl.get("max_daily_trades", 20)
        self._max_open_positions = rl.get("max_open_positions", 15)
        self._max_drawdown_hard_stop = rl.get("max_drawdown_hard_stop", -0.20)
        self._consecutive_loss_pause = rl.get("consecutive_loss_pause_count", 3)
        self._consecutive_loss_seconds = rl.get("consecutive_loss_pause_seconds", 3600)
        self._max_sector_pct = rl.get("max_sector_concentration_pct", 0.30)
        self._max_liquidity_pct = rl.get("max_liquidity_pct", 0.01)
        self._max_single_trade_loss_pct = float(rl.get("max_single_trade_loss_pct", 0.02))
        self._regime_multipliers = rl.get("regime_sizing_multipliers", {
            "bull": 1.0, "sideways": 0.75, "bear": 0.5, "high_vol": 0.5, "insufficient_data": 0.5,
        })

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
            # / catch-all: mark signal as error so it doesn't retry forever
            logger.error("risk_process_signal_error", signal_id=signal_id, error=str(exc))
            try:
                await tools.update_trade_status(pool, "trade_signals", signal_id, "error")
            except Exception as inner:
                # / db write failure: signal stuck pending, will retry next loop
                logger.warning(
                    "risk_signal_status_update_failed",
                    signal_id=signal_id, error=str(inner)[:120],
                )
            return {"status": "error", "reason": str(exc)}

    async def _process_signal_inner(
        self, pool, signal_id: int, broker: BrokerInterface,
        strategy_pool=None,
    ) -> dict:
        # / fetch signal
        async with pool.acquire() as conn:
            signal = await conn.fetchrow(
                "SELECT * FROM trade_signals WHERE id = $1 AND status = 'pending'",
                signal_id,
            )
        if not signal:
            return {"status": "skipped", "reason": "signal_not_found_or_not_pending"}

        signal = dict(signal)
        symbol = signal["symbol"]
        side = signal["signal_type"]
        # / clamp to [0, 1] to prevent oversized positions from malformed data
        strength = max(0.0, min(1.0, float(signal["strength"]) if signal["strength"] else 0.5))

        # / long-only guard: reject sells that would create a short
        long_only = os.environ.get("LONG_ONLY", "true").lower() in ("true", "1", "yes")
        if long_only and side == "sell":
            positions_check = await broker.get_positions()
            held = next(
                (p for p in positions_check
                 if (p.symbol if hasattr(p, "symbol") else p.get("symbol")) == symbol),
                None,
            )
            if not held:
                await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                logger.info("long_only_rejected", symbol=symbol, signal_id=signal_id)
                return {"status": "rejected", "reason": "long_only_no_position"}
            # / cap sell qty to actual alpaca position — never go short
            held_qty = held.qty if hasattr(held, "qty") else float(held.get("qty", 0))
            signal_qty = float(signal.get("details", {}).get("qty", 0)) if signal.get("details") else 0
            if signal_qty > held_qty:
                signal["details"] = signal.get("details") or {}
                signal["details"]["qty"] = held_qty
                signal["details"]["qty_capped"] = True
                logger.info("sell_qty_capped", symbol=symbol, requested=signal_qty, capped_to=held_qty)

        # / get account state
        balance = await broker.get_account_balance()
        positions = await broker.get_positions()

        if balance.equity <= 0:
            await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
            return {"status": "rejected", "reason": "zero_equity"}

        # / lazy-init peak equity from db on first call
        global _peak_equity, _circuit_breaker_until
        if _peak_equity == 0.0 and balance.equity > 0:
            await _init_peak_equity(pool)

        # / reject buy if this strategy already holds this symbol
        # / different strategies can hold the same symbol independently
        if side == "buy":
            strategy_id = signal.get("strategy_id")
            if strategy_id:
                strat_positions = await tools.get_strategy_positions(pool, strategy_id=strategy_id, symbol=symbol)
                if strat_positions:
                    await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                    return {"status": "rejected", "reason": "already_holding"}
            else:
                # / fallback for signals without strategy_id
                existing_pos = [p for p in positions
                               if (p.symbol if hasattr(p, "symbol") else p.get("symbol")) == symbol]
                if existing_pos:
                    await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                    return {"status": "rejected", "reason": "already_holding"}

        # / get current price (needed for concentration check and sizing)
        try:
            price = await broker.get_price(symbol)
        except Exception:
            await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
            return {"status": "rejected", "reason": "no_price"}

        # / circuit breaker: drawdown from peak
        if side == "buy":
            _peak_equity = max(_peak_equity, balance.equity)
            try:
                await tools.store_peak_equity(pool, balance.equity, _peak_equity)
            except Exception as exc:
                logger.debug("store_peak_equity_failed", error=str(exc)[:80])
            if _peak_equity > 0:
                drawdown = (balance.equity - _peak_equity) / _peak_equity
                if drawdown < self._max_drawdown_hard_stop:
                    await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                    logger.warning("circuit_breaker_drawdown", drawdown=drawdown, threshold=self._max_drawdown_hard_stop)
                    return {"status": "rejected", "reason": f"circuit_breaker_drawdown ({drawdown:.2%})"}

            # / circuit breaker: consecutive losses
            if time.time() < _circuit_breaker_until:
                await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                return {"status": "rejected", "reason": "circuit_breaker_consecutive_losses"}
            recent_pnl = await tools.fetch_recent_pnl(pool, limit=self._consecutive_loss_pause)
            if len(recent_pnl) >= self._consecutive_loss_pause and all(p < 0 for p in recent_pnl):
                _circuit_breaker_until = time.time() + self._consecutive_loss_seconds
                await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                logger.warning("circuit_breaker_consecutive_losses", count=len(recent_pnl))
                return {"status": "rejected", "reason": "circuit_breaker_consecutive_losses"}

            # / liquidity check
            avg_vol = await tools.fetch_avg_volume(pool, symbol)
            if avg_vol and avg_vol > 0 and price > 0:
                trade_value = (balance.equity * self.max_position_pct * strength)
                daily_dollar_vol = avg_vol * price
                if trade_value > daily_dollar_vol * self._max_liquidity_pct:
                    await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                    return {"status": "rejected", "reason": "insufficient_liquidity"}

            # / sector concentration check
            sector = get_sector(symbol)
            if sector:
                sector_value = 0.0
                for p in positions:
                    p_sym = p.symbol if hasattr(p, "symbol") else p.get("symbol")
                    if get_sector(p_sym) == sector:
                        sector_value += p.market_value if hasattr(p, "market_value") else 0
                if balance.equity > 0 and sector_value / balance.equity > self._max_sector_pct:
                    await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                    return {"status": "rejected", "reason": f"sector_concentration ({sector})"}

        # / enforce risk limits for buys
        if side == "buy":
            # / cash reserve: current cash must be at least N% of equity
            if balance.cash < balance.equity * self._min_cash_reserve_pct:
                await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                return {"status": "rejected", "reason": "cash_reserve_insufficient"}

            # / max open positions
            if len(positions) >= self._max_open_positions:
                await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                return {"status": "rejected", "reason": f"max_positions ({self._max_open_positions}) reached"}

            # / max daily trades
            today_count = await tools.count_today_approved_trades(pool)
            if today_count >= self._max_daily_trades:
                await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                return {"status": "rejected", "reason": f"max_daily_trades ({self._max_daily_trades}) reached"}

            # / cross-strategy symbol concentration: cap at 2x single-strategy limit
            all_sym_positions = await tools.get_strategy_positions(pool, symbol=symbol)
            if all_sym_positions:
                total_held_value = sum(sp["qty"] for sp in all_sym_positions) * price
                max_concentration = self.max_position_pct * 2 * balance.equity
                if total_held_value >= max_concentration:
                    await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                    return {"status": "rejected", "reason": f"cross_strategy_concentration: {symbol}"}

        # / compute position size
        if side == "sell":
            # / sells use the strategy's actual held qty, not computed size
            signal_details = signal.get("details") or {}
            qty = int(float(signal_details.get("qty", 0)))
            if qty <= 0:
                # / fallback: sell entire strategy position
                strat_pos = await tools.get_strategy_positions(pool, strategy_id=signal.get("strategy_id"), symbol=symbol)
                qty = int(strat_pos[0]["qty"]) if strat_pos else 0
        else:
            # / phase 6 step 10: kelly-weighted sizing via capital_allocator
            # / returns allocated_weight per strategy; falls back to half max_pct
            # / when no allocation row exists (first week) or under-sampled history
            strategy_id = signal.get("strategy_id") or ""
            max_pct = await capital_allocator.get_allocation(
                pool, strategy_id, max_position_pct_default=self.max_position_pct,
            )
            qty = (balance.equity * max_pct * strength) / price
            qty = max(0, int(qty))  # / whole shares

        # / regime-aware sizing multiplier (buys only)
        regime_mult = 1.0
        if side == "buy":
            try:
                regime = await tools.fetch_latest_regime(pool)
                regime_label = regime.get("regime", "insufficient_data") if regime else "insufficient_data"
                regime_mult = self._regime_multipliers.get(regime_label, 0.5)
                qty = int(qty * regime_mult)
            except Exception as exc:
                # / regime lookup failed — proceed at full size (conservative default)
                logger.debug("regime_sizing_skipped", symbol=symbol, error=str(exc)[:100])

        # / beta-adjusted sizing (buys only)
        if side == "buy":
            try:
                beta = await tools.fetch_symbol_beta(pool, symbol)
                if beta is not None and beta > 1.5:
                    qty = max(1, int(qty * (1.0 / beta)))
                    logger.info("beta_adjusted", symbol=symbol, beta=beta, qty=qty)
            except Exception as exc:
                # / beta lookup failed — proceed without adjustment
                logger.debug("beta_sizing_skipped", symbol=symbol, error=str(exc)[:100])

        if qty <= 0:
            await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
            return {"status": "rejected", "reason": "qty_zero"}

        # / single-trade loss cap (buys only): size down so worst-case stop loss
        # / does not exceed max_single_trade_loss_pct of equity
        if side == "buy" and strategy_pool is not None and balance.equity > 0:
            stop_distance = self._infer_stop_distance(strategy_pool, signal.get("strategy_id"))
            if stop_distance and stop_distance > 0:
                max_loss_value = balance.equity * self._max_single_trade_loss_pct
                max_qty_by_loss = int(max_loss_value / (price * stop_distance))
                if max_qty_by_loss < qty:
                    logger.info(
                        "single_trade_loss_cap",
                        symbol=symbol,
                        stop_distance=round(stop_distance, 4),
                        original_qty=qty, capped_qty=max_qty_by_loss,
                    )
                    qty = max(0, max_qty_by_loss)
                    if qty <= 0:
                        await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                        return {"status": "rejected", "reason": "single_trade_loss_cap"}

        # / check total portfolio exposure (buys only — sells reduce exposure)
        if side == "buy":
            total_position_value = sum(p.market_value for p in positions)
            new_position_value = qty * price
            total_exposure = (total_position_value + new_position_value) / max(balance.equity, 1)

            if total_exposure > self.max_portfolio_risk:
                # / size down to fit within risk limit
                available = (self.max_portfolio_risk * balance.equity) - total_position_value
                if available <= 0:
                    await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                    return {"status": "rejected", "reason": "portfolio_risk_exceeded"}
                qty = max(0, int(available / price))
                if qty <= 0:
                    await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                    return {"status": "rejected", "reason": "portfolio_risk_exceeded"}

        # / copula tail dependence check (skip on small portfolios, buys only)
        if side == "buy" and len(positions) >= 5:
            try:
                tail_dep = await self._check_tail_dependence(pool, symbol, positions)
                if tail_dep is not None and tail_dep > self.tail_dep_threshold:
                    # / size down by 50%
                    qty = max(1, qty // 2)
                    logger.warning(
                        "tail_dependence_sizing_down",
                        symbol=symbol, tail_dep=tail_dep, new_qty=qty,
                    )
            except Exception as exc:
                # / copula failed — proceed with position-size-only check
                logger.warning("copula_check_failed", symbol=symbol, error=str(exc))

        # / approve trade
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
        # / returns stop distance as a fraction of entry price (e.g. 0.05 for 5%)
        # / uses strategy's exit_conditions.stop_loss:
        # /   fixed_pct → pct directly
        # /   atr-based → 2 * atr/price ≈ 2% (conservative default without live ATR)
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
                # / use a conservative 2% as the atr-equivalent worst case
                return 0.02
        except Exception as exc:
            logger.debug("stop_distance_infer_failed", strategy_id=strategy_id, error=str(exc)[:100])
        return None

    async def _check_tail_dependence(
        self, pool, symbol: str, positions: list,
    ) -> float | None:
        # / fit t-copula to portfolio returns and check tail dependence
        # / returns lambda_lower or None if insufficient data
        from src.quant.copula_models import student_t_copula_fit, tail_dependence_coefficient

        position_symbols = [p.symbol for p in positions] + [symbol]

        # / fetch returns for all symbols
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT symbol, date, close FROM market_data
                WHERE symbol = ANY($1)
                ORDER BY date DESC LIMIT $2""",
                position_symbols, 252 * len(position_symbols),
            )

        if not rows:
            return None

        # / pivot to returns matrix
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

        # / convert to pseudo-observations
        from scipy.stats import rankdata
        u_data = np.column_stack([
            rankdata(returns.iloc[:, j]) / (returns.shape[0] + 1)
            for j in range(returns.shape[1])
        ])

        # / fit t-copula
        nu, corr = student_t_copula_fit(u_data)
        td = tail_dependence_coefficient("student_t", (nu, corr))

        return td.get("lambda_lower", 0.0)
