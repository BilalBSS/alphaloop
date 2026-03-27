# / centralized symbol normalization and classification
# / internal: "AAPL", "BTC-USD" | alpaca: "AAPL", "BTC/USD"
# / universe names: strategies reference these instead of hardcoding tickers

from __future__ import annotations

_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-EUR", "-GBP")

EQUITY_UNIVERSE = [
    # / etfs
    "SPY", "QQQ",
    # / mega-cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    # / semiconductors
    "AMD", "AVGO", "QCOM", "MRVL", "ARM",
    # / cybersecurity + cloud
    "CRM", "ADBE", "PLTR", "NET", "CRWD", "SNOW", "DDOG", "MDB", "PANW", "ZS",
    # / fintech
    "SHOP", "XYZ", "COIN", "HOOD", "SOFI", "AFRM",
    # / consumer tech
    "ABNB", "UBER", "DASH", "DUOL",
    # / health + clean energy
    "HIMS", "LLY", "MRNA", "ENPH", "FSLR", "ON",
    # / space
    "ASTS", "RKLB", "LUNR",
]
CRYPTO_UNIVERSE = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "AVAX-USD", "SUI-USD", "RENDER-USD",
]
FULL_UNIVERSE = EQUITY_UNIVERSE + CRYPTO_UNIVERSE

# / named universes — strategies reference these by name
# / the resolver expands names to symbol lists at runtime
# / "all" means scan everything in the database
NAMED_UNIVERSES: dict[str, list[str]] = {
    "sp500": [],       # / resolved at runtime from market_data table
    "nasdaq100": [],   # / resolved at runtime from market_data table
    "crypto": CRYPTO_UNIVERSE,
    "default_equity": EQUITY_UNIVERSE,
    "default_crypto": CRYPTO_UNIVERSE,
    "all": [],         # / resolved at runtime — every symbol in the database
}

# / valid universe reference names for strategy configs
VALID_UNIVERSE_REFS = {"sp500", "nasdaq100", "crypto", "default_equity", "default_crypto", "all", "all_stocks", "all_crypto"}


def resolve_universe(universe_ref: str, available_symbols: list[str] | None = None) -> list[str]:
    # / resolve a universe reference to a list of symbols
    # / available_symbols: all symbols currently in the database (passed by caller)
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
        # / these require real constituent lists — not yet maintained
        raise NotImplementedError(
            f"universe '{ref}' requires a constituent list that isn't maintained yet. "
            "Use 'all_stocks' or 'all' instead."
        )
    elif ref in NAMED_UNIVERSES:
        cached = NAMED_UNIVERSES[ref]
        if cached:
            return cached
        # / empty list means resolve from available_symbols
        if available_symbols:
            if ref in ("default_equity",):
                return [s for s in available_symbols if not is_crypto(s)]
            elif ref in ("crypto", "default_crypto"):
                return [s for s in available_symbols if is_crypto(s)]
        return EQUITY_UNIVERSE if ref == "default_equity" else FULL_UNIVERSE
    else:
        # / treat as a comma-separated list of specific symbols
        return [s.strip().upper() for s in universe_ref.split(",") if s.strip()]


def to_alpaca(symbol: str) -> str:
    # / "BTC-USD" -> "BTC/USD", equities pass through
    if is_crypto(symbol):
        return symbol.replace("-", "/")
    return symbol


def from_alpaca(symbol: str) -> str:
    # / "BTC/USD" -> "BTC-USD", equities pass through
    if "/" in symbol and is_crypto(symbol):
        return symbol.replace("/", "-")
    return symbol


def is_crypto(symbol: str) -> bool:
    # / true for crypto symbols in either format
    upper = symbol.upper()
    if "/" in upper:
        return True
    return any(upper.endswith(s) for s in _CRYPTO_SUFFIXES)


def market_type(symbol: str) -> str:
    # / returns "crypto" or "equity"
    return "crypto" if is_crypto(symbol) else "equity"
