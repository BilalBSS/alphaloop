# / crypto market data: coingecko (price/mcap/volume) + defillama (tvl/dex volume)
# / free apis, no key needed for defillama. coingecko free tier: 10-30 req/min.

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from .resilience import api_get, configure_rate_limit, with_retry

logger = structlog.get_logger(__name__)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
DEFILLAMA_BASE = "https://api.llama.fi"

configure_rate_limit("coingecko", max_concurrent=2, delay=2.5)
configure_rate_limit("defillama", max_concurrent=5, delay=0.5)

# / coingecko id map for common crypto symbols
_CG_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "ADA": "cardano", "DOT": "polkadot", "AVAX": "avalanche-2",
    "MATIC": "matic-network", "LINK": "chainlink", "UNI": "uniswap",
    "DOGE": "dogecoin", "XRP": "ripple", "ATOM": "cosmos",
}


def _cg_id(symbol: str) -> str | None:
    sym = symbol.upper().replace("-USD", "")
    return _CG_IDS.get(sym)


@with_retry(source="coingecko", max_retries=2, base_delay=3.0)
async def fetch_coin_data(symbol: str) -> dict[str, Any] | None:
    # / fetch price, market_cap, volume, price_change from coingecko
    cg_id = _cg_id(symbol)
    if not cg_id:
        return None

    url = f"{COINGECKO_BASE}/coins/{cg_id}"
    params = {
        "localization": "false",
        "tickers": "false",
        "community_data": "false",
        "developer_data": "false",
    }
    resp = await api_get(url, params=params, source="coingecko")
    data = resp.json()

    market = data.get("market_data", {})
    return {
        "symbol": symbol,
        "price": market.get("current_price", {}).get("usd"),
        "market_cap": market.get("market_cap", {}).get("usd"),
        "total_volume": market.get("total_volume", {}).get("usd"),
        "price_change_24h_pct": market.get("price_change_percentage_24h"),
        "price_change_7d_pct": market.get("price_change_percentage_7d"),
        "circulating_supply": market.get("circulating_supply"),
    }


@with_retry(source="coingecko", max_retries=2, base_delay=3.0)
async def fetch_coin_market_chart(
    symbol: str, days: int = 30,
) -> dict[str, Any] | None:
    # / fetch historical price + volume + market_cap series
    cg_id = _cg_id(symbol)
    if not cg_id:
        return None

    url = f"{COINGECKO_BASE}/coins/{cg_id}/market_chart"
    params = {"vs_currency": "usd", "days": str(days)}
    resp = await api_get(url, params=params, source="coingecko")
    data = resp.json()

    return {
        "symbol": symbol,
        "prices": data.get("prices", []),
        "market_caps": data.get("market_caps", []),
        "total_volumes": data.get("total_volumes", []),
    }


@with_retry(source="defillama", max_retries=2, base_delay=1.0)
async def fetch_defi_tvl(protocol: str | None = None) -> dict[str, Any]:
    # / fetch total value locked — all protocols or specific one
    if protocol:
        url = f"{DEFILLAMA_BASE}/protocol/{protocol}"
    else:
        url = f"{DEFILLAMA_BASE}/protocols"
    resp = await api_get(url, source="defillama")
    return resp.json()


@with_retry(source="defillama", max_retries=2, base_delay=1.0)
async def fetch_dex_volume(chain: str | None = None) -> dict[str, Any]:
    # / fetch dex trading volume
    url = f"{DEFILLAMA_BASE}/overview/dexs"
    params = {}
    if chain:
        params["chain"] = chain
    resp = await api_get(url, params=params, source="defillama")
    return resp.json()


@with_retry(source="defillama", max_retries=2, base_delay=1.0)
async def fetch_stablecoin_supply() -> dict[str, Any]:
    # / fetch stablecoin market cap data
    url = f"{DEFILLAMA_BASE}/stablecoins"
    resp = await api_get(url, source="defillama")
    return resp.json()
