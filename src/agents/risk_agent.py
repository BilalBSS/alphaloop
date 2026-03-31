# / risk agent — evaluates trade signals for portfolio risk before approving
# / uses copula-based tail dependence for correlation risk
# / skips copula on small portfolios (< 5 positions or < 10 days history)

from __future__ import annotations

import os

import numpy as np
import structlog

from src.agents import tools
from src.brokers.base import BrokerInterface

logger = structlog.get_logger(__name__)


class RiskAgent:
    def __init__(
        self,
        max_position_pct: float | None = None,
        max_portfolio_risk: float | None = None,
        tail_dep_threshold: float = 0.30,
    ):
        self.max_position_pct = max_position_pct or float(
            os.environ.get("MAX_POSITION_PCT", "0.08")
        )
        self.max_portfolio_risk = max_portfolio_risk or float(
            os.environ.get("MAX_PORTFOLIO_RISK", "0.25")
        )
        self.tail_dep_threshold = tail_dep_threshold

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
            except Exception:
                pass
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

        # / long-only guard: reject naked sells (shorts)
        long_only = os.environ.get("LONG_ONLY", "true").lower() in ("true", "1", "yes")
        if long_only and side == "sell":
            positions_check = await broker.get_positions()
            has_position = any(
                (p.symbol if hasattr(p, "symbol") else p.get("symbol")) == symbol
                for p in positions_check
            )
            if not has_position:
                await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
                logger.info("long_only_rejected", symbol=symbol, signal_id=signal_id)
                return {"status": "rejected", "reason": "long_only_no_position"}

        # / get account state
        balance = await broker.get_account_balance()
        positions = await broker.get_positions()

        if balance.equity <= 0:
            await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
            return {"status": "rejected", "reason": "zero_equity"}

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

        # / get current price
        try:
            price = await broker.get_price(symbol)
        except Exception:
            await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
            return {"status": "rejected", "reason": "no_price"}

        # / compute position size
        max_pct = self.max_position_pct
        qty = (balance.equity * max_pct * strength) / price
        qty = max(0, int(qty))  # / whole shares

        if qty <= 0:
            await tools.update_trade_status(pool, "trade_signals", signal_id, "rejected")
            return {"status": "rejected", "reason": "qty_zero"}

        # / check total portfolio exposure
        total_position_value = sum(p.market_value for p in positions)
        new_position_value = qty * price
        total_exposure = (total_position_value + new_position_value) / balance.equity

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

        # / copula tail dependence check (skip on small portfolios)
        if len(positions) >= 5:
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
