# / aggregate crypto fundamentals (nvt, funding, tvl, active addrs,
# / exchange flows, hash rate, dex volume, stablecoin supply ratio). every source
# / is best-effort — one failure returns null for that field only, never 500s the
# / whole response.

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

import structlog

from src.data.crypto_data import (
    fetch_coin_data,
    fetch_defi_tvl,
    fetch_dex_volume,
    fetch_funding_rates,
    fetch_stablecoin_supply,
    get_funding_rate,
)
from src.data.crypto_onchain import (
    fetch_active_addresses,
    fetch_exchange_flows,
)

logger = structlog.get_logger(__name__)

# / symbol -> defillama chain slug for tvl + dex_volume lookups
_DEFILLAMA_CHAIN = {
    "ETH": "ethereum",
    "SOL": "solana",
    "AVAX": "avalanche",
    "SUI": "sui",
    "XRP": "xrp",
    "HYPE": "hyperliquid",
    "RENDER": "render",
}


def _base_symbol(symbol: str) -> str:
    # / "BTC-USD" -> "BTC"; preserves case for passthrough tokens
    return symbol.upper().replace("-USD", "").replace("/USD", "")


def _hash_rate_from_coingecko(coin_data: dict[str, Any] | None) -> float | None:
    # / coingecko doesn't expose hash_rate directly in the coins endpoint we fetch,
    # / and a standalone hash-rate api isn't part of the free tier. leave null so
    # / the widget renders "—" for symbols without a feed.
    return None


def _extract_nvt(coin_data: dict[str, Any] | None) -> float | None:
    # / nvt = market_cap / total_volume. a rough proxy when a dedicated nvt feed
    # / isn't configured. returns null for missing / zero volume.
    if not coin_data:
        return None
    mcap = coin_data.get("market_cap")
    vol = coin_data.get("total_volume")
    try:
        mcap_f = float(mcap) if mcap is not None else None
        vol_f = float(vol) if vol is not None else None
    except (TypeError, ValueError):
        return None
    if not mcap_f or not vol_f:
        return None
    return round(mcap_f / vol_f, 4)


def _extract_tvl(tvl_data: Any, sym: str) -> float | None:
    # / pull the chain-level tvl for the symbol if defillama returns a chain slug
    chain_slug = _DEFILLAMA_CHAIN.get(sym)
    if not chain_slug or not tvl_data:
        return None
    try:
        # / /protocols returns a list; /protocol/<slug> returns a dict with "tvl"
        if isinstance(tvl_data, dict):
            tvl_val = tvl_data.get("tvl")
            if isinstance(tvl_val, (int, float)):
                return float(tvl_val)
            # / /protocol/<slug> sometimes nests chain tvl under chains.<chain>
            chains = tvl_data.get("chainTvls") or tvl_data.get("chains")
            if isinstance(chains, dict):
                val = chains.get(chain_slug) or chains.get(chain_slug.capitalize())
                if isinstance(val, (int, float)):
                    return float(val)
        if isinstance(tvl_data, list):
            # / sum tvl across protocols whose chain matches
            total = 0.0
            matched = False
            for p in tvl_data:
                if not isinstance(p, dict):
                    continue
                chain = (p.get("chain") or "").lower()
                if chain == chain_slug.lower():
                    v = p.get("tvl")
                    if isinstance(v, (int, float)):
                        total += float(v)
                        matched = True
            return round(total, 2) if matched else None
    except Exception as exc:
        logger.debug("tvl_extract_failed", symbol=sym, error=str(exc)[:120])
    return None


def _extract_dex_volume(dex_data: Any, sym: str) -> float | None:
    # / defillama /overview/dexs returns totalVolume + per-chain breakdown. we
    # / prefer the chain-filtered response when possible.
    if not dex_data or not isinstance(dex_data, dict):
        return None
    try:
        # / when fetched with chain=<slug>, totalVolume reflects that chain only
        val = dex_data.get("total24h") or dex_data.get("totalVolume")
        if isinstance(val, (int, float)):
            return round(float(val), 2)
    except Exception as exc:
        logger.debug("dex_volume_extract_failed", symbol=sym, error=str(exc)[:120])
    return None


def _extract_stablecoin_ratio(supply_data: Any) -> float | None:
    # / ratio = sum(stablecoin market cap) / sum(non-stable crypto market cap).
    # / defillama /stablecoins gives total stable supply; we approximate the
    # / ratio as sum of peggedAssets circulating / 2e12 (total crypto mcap proxy).
    # / if the data doesn't have enough shape, return null.
    if not supply_data or not isinstance(supply_data, dict):
        return None
    try:
        pegged = supply_data.get("peggedAssets")
        if not isinstance(pegged, list) or not pegged:
            return None
        total_stable = 0.0
        for p in pegged:
            if not isinstance(p, dict):
                continue
            circ = p.get("circulating") or {}
            if isinstance(circ, dict):
                v = circ.get("peggedUSD") or circ.get("peggedusd")
                if isinstance(v, (int, float)):
                    total_stable += float(v)
        if total_stable <= 0:
            return None
        # / crude ratio vs a static 2.5t crypto mcap baseline so the widget can
        # / show a plausible percentage. actual ssr definitions vary by vendor.
        baseline = 2.5e12
        return round(total_stable / baseline, 6)
    except Exception as exc:
        logger.debug("stablecoin_ratio_extract_failed", error=str(exc)[:120])
    return None


def _extract_active_addresses(rows: list[dict[str, Any]] | None, sym: str) -> int | None:
    # / dune active-addresses query returns rows like {date, chain, active_addresses}
    if not rows:
        return None
    try:
        latest = rows[0]
        val = latest.get("active_addresses") or latest.get("addresses") or latest.get("count")
        if isinstance(val, (int, float)):
            return int(val)
    except Exception as exc:
        logger.debug("active_addr_extract_failed", symbol=sym, error=str(exc)[:120])
    return None


def _extract_exchange_inflow(rows: list[dict[str, Any]] | None, sym: str) -> float | None:
    # / dune exchange-flows query returns rows like {date, token, inflow_usd, outflow_usd}
    if not rows:
        return None
    try:
        latest = rows[0]
        inflow = latest.get("inflow_usd") or latest.get("net_inflow_usd") or latest.get("inflow")
        outflow = latest.get("outflow_usd") or latest.get("outflow")
        if isinstance(inflow, (int, float)) and isinstance(outflow, (int, float)):
            return round(float(inflow) - float(outflow), 2)
        if isinstance(inflow, (int, float)):
            return round(float(inflow), 2)
    except Exception as exc:
        logger.debug("exchange_flow_extract_failed", symbol=sym, error=str(exc)[:120])
    return None


async def fetch_live_fundamentals(symbol: str) -> dict[str, Any]:
    # / best-effort live fetch across all configured crypto data sources.
    # / every source is wrapped in its own try — one 500 doesn't kill the response.
    sym = _base_symbol(symbol)
    out: dict[str, Any] = {
        "nvt_ratio": None,
        "funding_rate": None,
        "active_addresses": None,
        "exchange_inflow_usd": None,
        "hash_rate": None,
        "tvl_usd": None,
        "dex_volume_24h": None,
        "stablecoin_supply_ratio": None,
    }
    sources: list[str] = []

    # / coingecko coin data -> nvt proxy (mcap/volume)
    try:
        coin = await fetch_coin_data(symbol)
        nvt = _extract_nvt(coin)
        if nvt is not None:
            out["nvt_ratio"] = nvt
            sources.append("coingecko")
        # / hash_rate placeholder — only BTC is meaningful; left null until a feed lands
        if sym == "BTC":
            hr = _hash_rate_from_coingecko(coin)
            if hr is not None:
                out["hash_rate"] = hr
    except Exception as exc:
        logger.warning("crypto_fundamentals_coingecko_failed", symbol=symbol, error=str(exc)[:120])

    # / loris funding rates (free, no key)
    try:
        fr_data = await fetch_funding_rates()
        fr = get_funding_rate(fr_data, symbol) if fr_data else None
        if fr and fr.get("funding_rate") is not None:
            # / convert to annualized % (8h funding * 3 * 365 ~ 1095)
            annualized = float(fr["funding_rate"]) * 1095
            out["funding_rate"] = round(annualized, 6)
            if "loris" not in sources:
                sources.append("loris")
    except Exception as exc:
        logger.warning("crypto_fundamentals_funding_failed", symbol=symbol, error=str(exc)[:120])

    # / defillama tvl + dex volume (no key)
    chain_slug = _DEFILLAMA_CHAIN.get(sym)
    if chain_slug:
        try:
            tvl_raw = await fetch_defi_tvl()
            tvl = _extract_tvl(tvl_raw, sym)
            if tvl is not None:
                out["tvl_usd"] = tvl
                if "defillama" not in sources:
                    sources.append("defillama")
        except Exception as exc:
            logger.warning("crypto_fundamentals_tvl_failed", symbol=symbol, error=str(exc)[:120])
        try:
            dex_raw = await fetch_dex_volume(chain=chain_slug)
            dv = _extract_dex_volume(dex_raw, sym)
            if dv is not None:
                out["dex_volume_24h"] = dv
                if "defillama" not in sources:
                    sources.append("defillama")
        except Exception as exc:
            logger.warning("crypto_fundamentals_dex_volume_failed", symbol=symbol, error=str(exc)[:120])

    try:
        ssr_raw = await fetch_stablecoin_supply()
        ssr = _extract_stablecoin_ratio(ssr_raw)
        if ssr is not None:
            out["stablecoin_supply_ratio"] = ssr
            if "defillama" not in sources:
                sources.append("defillama")
    except Exception as exc:
        logger.warning("crypto_fundamentals_stablecoin_failed", symbol=symbol, error=str(exc)[:120])

    # / dune — active addresses + exchange flows. silently skip if DUNE_API_KEY is absent
    if os.environ.get("DUNE_API_KEY"):
        try:
            rows = await fetch_active_addresses(chain=chain_slug or sym.lower())
            val = _extract_active_addresses(rows, sym)
            if val is not None:
                out["active_addresses"] = val
                if "dune" not in sources:
                    sources.append("dune")
        except Exception as exc:
            logger.warning("crypto_fundamentals_dune_addr_failed", symbol=symbol, error=str(exc)[:120])
        try:
            rows = await fetch_exchange_flows(symbol=sym)
            val = _extract_exchange_inflow(rows, sym)
            if val is not None:
                out["exchange_inflow_usd"] = val
                if "dune" not in sources:
                    sources.append("dune")
        except Exception as exc:
            logger.warning("crypto_fundamentals_dune_flows_failed", symbol=symbol, error=str(exc)[:120])
    else:
        logger.debug("crypto_fundamentals_dune_skipped", symbol=symbol, reason="no_api_key")

    out["sources"] = sources
    return out


async def load_cached_fundamentals(pool, symbol: str) -> dict[str, Any] | None:
    # / return the most recent cached row for a symbol, or None if absent / stale (>36h).
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT symbol, date, nvt_ratio, funding_rate, active_addresses,
                          exchange_inflow_usd, hash_rate, tvl_usd, dex_volume_24h,
                          stablecoin_supply_ratio, sources, updated_at
                FROM crypto_fundamentals
                WHERE symbol = $1
                  AND updated_at >= NOW() - INTERVAL '36 hours'
                ORDER BY date DESC LIMIT 1""",
                symbol.upper(),
            )
    except Exception as exc:
        msg = str(exc).lower()
        if "does not exist" in msg or "undefined" in msg:
            # / migration not yet applied — bubble up as "no cache" silently
            return None
        logger.warning("crypto_fundamentals_cache_read_failed", symbol=symbol, error=str(exc)[:120])
        return None
    if not row:
        return None
    return dict(row)


async def upsert_fundamentals(pool, symbol: str, data: dict[str, Any]) -> None:
    # / write the aggregate into crypto_fundamentals; caller handles fetcher errors
    if pool is None:
        return
    sym = symbol.upper()
    today = date.today()
    import json as _json
    sources_json = _json.dumps(data.get("sources") or [])
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO crypto_fundamentals (
                symbol, date, nvt_ratio, funding_rate, active_addresses,
                exchange_inflow_usd, hash_rate, tvl_usd, dex_volume_24h,
                stablecoin_supply_ratio, sources, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, NOW())
            ON CONFLICT (symbol, date) DO UPDATE SET
                nvt_ratio = EXCLUDED.nvt_ratio,
                funding_rate = EXCLUDED.funding_rate,
                active_addresses = EXCLUDED.active_addresses,
                exchange_inflow_usd = EXCLUDED.exchange_inflow_usd,
                hash_rate = EXCLUDED.hash_rate,
                tvl_usd = EXCLUDED.tvl_usd,
                dex_volume_24h = EXCLUDED.dex_volume_24h,
                stablecoin_supply_ratio = EXCLUDED.stablecoin_supply_ratio,
                sources = EXCLUDED.sources,
                updated_at = NOW()""",
            sym, today,
            data.get("nvt_ratio"), data.get("funding_rate"),
            data.get("active_addresses"), data.get("exchange_inflow_usd"),
            data.get("hash_rate"), data.get("tvl_usd"),
            data.get("dex_volume_24h"), data.get("stablecoin_supply_ratio"),
            sources_json,
        )


async def get_fundamentals(pool, symbol: str) -> dict[str, Any]:
    # / primary entry point for the endpoint: cache-first, fall back to live fetch.
    # / always returns the full shape (nulls where a source failed).
    sym = symbol.upper()
    cached = await load_cached_fundamentals(pool, sym)
    if cached:
        # / normalize types: decimal -> float, date -> iso, jsonb sources -> list
        sources_raw = cached.get("sources")
        if isinstance(sources_raw, str):
            try:
                import json as _json
                sources_raw = _json.loads(sources_raw)
            except (ValueError, TypeError):
                sources_raw = []
        updated = cached.get("updated_at")
        return {
            "nvt_ratio": _to_float(cached.get("nvt_ratio")),
            "funding_rate": _to_float(cached.get("funding_rate")),
            "active_addresses": int(cached["active_addresses"]) if cached.get("active_addresses") is not None else None,
            "exchange_inflow_usd": _to_float(cached.get("exchange_inflow_usd")),
            "hash_rate": _to_float(cached.get("hash_rate")),
            "tvl_usd": _to_float(cached.get("tvl_usd")),
            "dex_volume_24h": _to_float(cached.get("dex_volume_24h")),
            "stablecoin_supply_ratio": _to_float(cached.get("stablecoin_supply_ratio")),
            "sources": sources_raw if isinstance(sources_raw, list) else [],
            "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else (updated or datetime.now(timezone.utc).isoformat()),
        }

    # / no cache — fetch live and opportunistically persist
    data = await fetch_live_fundamentals(sym)
    try:
        await upsert_fundamentals(pool, sym, data)
    except Exception as exc:
        msg = str(exc).lower()
        if "does not exist" not in msg and "undefined" not in msg:
            logger.warning("crypto_fundamentals_cache_write_failed", symbol=sym, error=str(exc)[:120])
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    return data


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
