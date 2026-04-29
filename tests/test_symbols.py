"""Tests for src/data/symbols.py — symbol normalization and classification."""

import pytest

from src.data.symbols import (
    CRYPTO_UNIVERSE,
    EQUITY_UNIVERSE,
    FULL_UNIVERSE,
    is_crypto,
    market_type,
    resolve_universe,
    to_alpaca,
)


class TestToAlpaca:
    """Convert internal format to Alpaca API format."""

    def test_crypto_btc(self):
        assert to_alpaca("BTC-USD") == "BTC/USD"

    def test_crypto_eth(self):
        assert to_alpaca("ETH-USD") == "ETH/USD"

    def test_equity_passthrough(self):
        assert to_alpaca("AAPL") == "AAPL"

    def test_equity_spy(self):
        assert to_alpaca("SPY") == "SPY"

    def test_crypto_sol(self):
        assert to_alpaca("SOL-USD") == "SOL/USD"

    def test_crypto_usdt_pair(self):
        assert to_alpaca("BTC-USDT") == "BTC/USDT"


class TestIsCrypto:
    """Classify symbols as crypto or equity."""

    def test_btc_internal(self):
        assert is_crypto("BTC-USD") is True

    def test_eth_internal(self):
        assert is_crypto("ETH-USD") is True

    def test_btc_alpaca(self):
        assert is_crypto("BTC/USD") is True

    def test_equity(self):
        assert is_crypto("AAPL") is False

    def test_spy(self):
        assert is_crypto("SPY") is False

    def test_sol(self):
        assert is_crypto("SOL-USD") is True

    def test_usdt_pair(self):
        assert is_crypto("BTC-USDT") is True

    def test_case_insensitive(self):
        assert is_crypto("btc-usd") is True

    def test_eur_pair(self):
        assert is_crypto("BTC-EUR") is True

    def test_gbp_pair(self):
        assert is_crypto("BTC-GBP") is True

    def test_lowercase_with_slash(self):
        assert is_crypto("btc/usd") is True


class TestMarketType:
    def test_crypto(self):
        assert market_type("BTC-USD") == "crypto"

    def test_equity(self):
        assert market_type("AAPL") == "equity"


class TestUniverseConstants:
    def test_equity_universe_not_empty(self):
        assert len(EQUITY_UNIVERSE) > 0

    def test_crypto_universe_not_empty(self):
        assert len(CRYPTO_UNIVERSE) > 0

    def test_full_universe_is_combined(self):
        assert FULL_UNIVERSE == EQUITY_UNIVERSE + CRYPTO_UNIVERSE

    def test_no_crypto_in_equity_universe(self):
        for sym in EQUITY_UNIVERSE:
            assert not is_crypto(sym), f"{sym} should not be crypto"

    def test_all_crypto_in_crypto_universe(self):
        for sym in CRYPTO_UNIVERSE:
            assert is_crypto(sym), f"{sym} should be crypto"


class TestResolveUniverse:
    def test_all_with_available_symbols(self):
        available = ["AAPL", "BTC-USD", "TSLA"]
        result = resolve_universe("all", available_symbols=available)
        assert result == available

    def test_all_without_available_symbols(self):
        result = resolve_universe("all")
        assert result == FULL_UNIVERSE

    def test_all_stocks_filters_out_crypto(self):
        available = ["AAPL", "BTC-USD", "MSFT", "ETH-USD"]
        result = resolve_universe("all_stocks", available_symbols=available)
        assert result == ["AAPL", "MSFT"]

    def test_all_crypto_filters_out_stocks(self):
        available = ["AAPL", "BTC-USD", "MSFT", "ETH-USD"]
        result = resolve_universe("all_crypto", available_symbols=available)
        assert result == ["BTC-USD", "ETH-USD"]

    def test_sp500_raises_not_implemented(self):
        with pytest.raises(NotImplementedError):
            resolve_universe("sp500")

    def test_nasdaq100_raises_not_implemented(self):
        with pytest.raises(NotImplementedError):
            resolve_universe("nasdaq100")

    def test_comma_separated_string(self):
        result = resolve_universe("AAPL,MSFT,GOOG")
        assert result == ["AAPL", "MSFT", "GOOG"]

    def test_unknown_ref_treated_as_comma_separated(self):
        result = resolve_universe("TSLA")
        assert result == ["TSLA"]

    def test_whitespace_handling_in_comma_separated(self):
        result = resolve_universe(" AAPL , MSFT , GOOG ")
        assert result == ["AAPL", "MSFT", "GOOG"]

    def test_case_insensitive_ref(self):
        result = resolve_universe("ALL")
        assert result == FULL_UNIVERSE

    def test_default_equity_with_available_symbols(self):
        # / "default_equity" has a cached list (EQUITY_UNIVERSE), returns it directly
        available = ["AAPL", "BTC-USD", "TSLA"]
        result = resolve_universe("default_equity", available_symbols=available)
        assert result == EQUITY_UNIVERSE

    def test_crypto_returns_crypto_universe(self):
        result = resolve_universe("crypto")
        assert result == CRYPTO_UNIVERSE


class TestSectors:
    def test_get_sector_returns_correct_sector(self):
        from src.data.symbols import get_sector
        assert get_sector("PLTR") == "cloud_cyber"
        assert get_sector("NVDA") == "semis"
        assert get_sector("BTC-USD") == "large_crypto"
        assert get_sector("SPY") == "etfs"

    def test_get_sector_returns_none_for_unknown(self):
        from src.data.symbols import get_sector
        assert get_sector("NONEXISTENT") is None
        assert get_sector("") is None

    def test_get_sector_symbols_returns_list(self):
        from src.data.symbols import get_sector_symbols
        syms = get_sector_symbols("semis")
        assert "NVDA" in syms
        assert "AMD" in syms
        assert len(syms) == 6

    def test_get_sector_symbols_unknown_returns_empty(self):
        from src.data.symbols import get_sector_symbols
        assert get_sector_symbols("nonexistent") == []

    def test_resolve_universe_with_sector_name(self):
        result = resolve_universe("cloud_cyber")
        assert "PLTR" in result
        assert "NET" in result
        assert len(result) == 10

    def test_all_symbols_have_sectors(self):
        from src.data.symbols import SECTORS, get_sector
        all_sectored = []
        for syms in SECTORS.values():
            all_sectored.extend(syms)
        # / every symbol in FULL_UNIVERSE should be in a sector
        for sym in FULL_UNIVERSE:
            assert get_sector(sym) is not None, f"{sym} has no sector"

    def test_sectors_cover_full_universe(self):
        from src.data.symbols import SECTORS
        all_sectored = set()
        for syms in SECTORS.values():
            all_sectored.update(syms)
        # / TSLA is in EQUITY_UNIVERSE but in mega_tech? let's check
        for sym in FULL_UNIVERSE:
            assert sym in all_sectored, f"{sym} missing from SECTORS"
