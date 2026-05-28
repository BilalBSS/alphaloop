
from __future__ import annotations

_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-EUR", "-GBP")

EQUITY_UNIVERSE = [
    # /
    "SPY", "QQQ", "SPUS",
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "AMD", "AVGO", "QCOM", "MRVL", "ARM", "MU", "WDC",
    "CRM", "PLTR", "NET", "CRWD", "SNOW", "DDOG", "MDB",
    "SHOP", "XYZ", "COIN", "HOOD", "SOFI",
    "HIMS", "RKLB", "ASTS",
    # / stable enterprise cyber/cloud
    "ADBE", "PANW", "ZS",
    # / fintech tail
    "AFRM",
    # / consumer tech
    "ABNB", "UBER", "DASH", "DUOL",
    # / health + clean energy
    "LLY", "MRNA", "ENPH", "FSLR", "ON",
    # / space tail
    "LUNR",
    # / commodities (low movement)
    "GLD", "SLV",
]
CRYPTO_UNIVERSE = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "HYPE-USD", "AVAX-USD", "SUI-USD", "RENDER-USD",
]
FULL_UNIVERSE = EQUITY_UNIVERSE + CRYPTO_UNIVERSE

SECTORS: dict[str, list[str]] = {
    "mega_tech":     ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"],
    "semis":         ["NVDA", "AMD", "AVGO", "QCOM", "MRVL", "ARM", "MU", "WDC"],
    "cloud_cyber":   ["CRM", "ADBE", "PLTR", "NET", "CRWD", "SNOW", "DDOG", "MDB", "PANW", "ZS"],
    "fintech":       ["SHOP", "XYZ", "COIN", "HOOD", "SOFI", "AFRM"],
    "consumer":      ["ABNB", "UBER", "DASH", "DUOL"],
    "health_energy": ["HIMS", "LLY", "MRNA", "ENPH", "FSLR", "ON"],
    "space":         ["ASTS", "RKLB", "LUNR"],
    "commodities":   ["GLD", "SLV"],
    "large_crypto":  ["BTC-USD", "ETH-USD"],
    "alt_crypto":    ["SOL-USD", "XRP-USD", "HYPE-USD", "AVAX-USD", "SUI-USD", "RENDER-USD"],
    "etfs":          ["SPY", "QQQ", "SPUS"],
}

_SYMBOL_TO_SECTOR: dict[str, str] = {}
for _sec, _syms in SECTORS.items():
    for _sym in _syms:
        _SYMBOL_TO_SECTOR[_sym] = _sec


def get_sector(symbol: str) -> str | None:
    return _SYMBOL_TO_SECTOR.get(symbol.upper())


def get_sector_symbols(sector: str) -> list[str]:
    return SECTORS.get(sector, [])


NAMED_UNIVERSES: dict[str, list[str]] = {
    "sp500": [],  # / resolved at runtime from
    "nasdaq100": [],  # / resolved at runtime from
    "crypto": CRYPTO_UNIVERSE,
    "default_equity": EQUITY_UNIVERSE,
    "default_crypto": CRYPTO_UNIVERSE,
    "all": [],  # / resolved at runtime —
}

VALID_UNIVERSE_REFS = {"sp500", "nasdaq100", "crypto", "default_equity", "default_crypto", "all", "all_stocks", "all_crypto"} | set(SECTORS.keys())


def resolve_universe(universe_ref: str, available_symbols: list[str] | None = None) -> list[str]:
    ref = universe_ref.lower().strip()

    if ref == "all":
        return available_symbols or FULL_UNIVERSE
    elif ref == "all_stocks":
        if available_symbols:
            return [s for s in available_symbols if not is_crypto(s)]
        return EQUITY_UNIVERSE
    elif ref == "all_crypto":
        if available_symbols:
            return [s for s in available_symbols if is_crypto(s)]
        return CRYPTO_UNIVERSE
    elif ref in ("sp500", "nasdaq100"):
        raise NotImplementedError(
            f"universe '{ref}' requires a constituent list that isn't maintained yet. "
            "Use 'all_stocks' or 'all' instead."
        )
    elif ref in SECTORS:
        return SECTORS[ref]
    elif ref in NAMED_UNIVERSES:
        cached = NAMED_UNIVERSES[ref]
        if cached:
            return cached
        if available_symbols:
            if ref in ("default_equity",):
                return [s for s in available_symbols if not is_crypto(s)]
            elif ref in ("crypto", "default_crypto"):
                return [s for s in available_symbols if is_crypto(s)]
        return EQUITY_UNIVERSE if ref == "default_equity" else FULL_UNIVERSE
    else:
        return [s.strip().upper() for s in universe_ref.split(",") if s.strip()]


def to_alpaca(symbol: str) -> str:
    if is_crypto(symbol):
        return symbol.replace("-", "/")
    return symbol


def is_crypto(symbol: str) -> bool:
    upper = symbol.upper()
    if "/" in upper:
        return True
    return any(upper.endswith(s) for s in _CRYPTO_SUFFIXES)


def market_type(symbol: str) -> str:
    # / returns "crypto" or "equity"
    return "crypto" if is_crypto(symbol) else "equity"
