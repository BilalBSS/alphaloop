# / crypto liquidations: tracks long/short liquidation imbalance
# / uses coinglass-style public endpoints

from __future__ import annotations

from datetime import date
from typing import Any

import structlog

from .resilience import api_get, configure_rate_limit, with_retry
from .symbols import is_crypto

logger = structlog.get_logger(__name__)

# / public liquidation data endpoint
LIQUIDATION_URL = "https://open-api.coinglass.com/public/v2/liquidation/info"

configure_rate_limit("coinglass", max_concurrent=2, delay=2.0)

# / symbol mapping for coinglass
_CG_SYMBOLS = {
    "BTC-USD": "BTC", "ETH-USD": "ETH", "SOL-USD": "SOL",
    "XRP-USD": "XRP", "AVAX-USD": "AVAX", "SUI-USD": "SUI",
    "HYPE-USD": "HYPE", "RENDER-USD": "RENDER",
}


def _to_cg_symbol(symbol: str) -> str | None:
    return _CG_SYMBOLS.get(symbol.upper())


@with_retry(source="coinglass", max_retries=2, base_delay=3.0)
async def fetch_liquidation_data(symbol: str) -> dict[str, Any] | None:
    if not is_crypto(symbol):
        return None
    cg_sym = _to_cg_symbol(symbol)
    if not cg_sym:
        return None
    try:
        params = {"symbol": cg_sym, "time_type": 2}
        resp = await api_get(LIQUIDATION_URL, params=params, source="coinglass")
        data = resp.json()
        info = data.get("data", {})
        if not info:
            return None
        long_liq = float(info.get("longLiquidationUsd", 0) or 0)
        short_liq = float(info.get("shortLiquidationUsd", 0) or 0)
        total = long_liq + short_liq
        imbalance = (long_liq - short_liq) / total if total > 0 else 0.0
        return {
            "symbol": symbol,
            "long_liquidations": long_liq,
            "short_liquidations": short_liq,
            "liquidation_imbalance": max(-1.0, min(1.0, imbalance)),
        }
    except Exception as exc:
        logger.debug("liquidation_fetch_failed", symbol=symbol, error=str(exc))
        return None


async def store_liquidation_data(pool: Any, data: dict[str, Any]) -> None:
    if not data:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO crypto_liquidations
                (symbol, date, long_liquidations, short_liquidations, liquidation_imbalance)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (symbol, date) DO UPDATE SET
                long_liquidations = EXCLUDED.long_liquidations,
                short_liquidations = EXCLUDED.short_liquidations,
                liquidation_imbalance = EXCLUDED.liquidation_imbalance
            """,
            data["symbol"], date.today(),
            data.get("long_liquidations"), data.get("short_liquidations"),
            data.get("liquidation_imbalance"),
        )
