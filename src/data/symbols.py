# / centralized symbol normalization and classification
# / internal: "AAPL", "BTC-USD" | alpaca: "AAPL", "BTC/USD"

from __future__ import annotations

_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-EUR", "-GBP")

EQUITY_UNIVERSE = ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]
CRYPTO_UNIVERSE = ["BTC-USD", "ETH-USD"]
FULL_UNIVERSE = EQUITY_UNIVERSE + CRYPTO_UNIVERSE


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
