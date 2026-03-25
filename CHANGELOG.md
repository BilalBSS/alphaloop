# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0.0] - 2026-03-24

### Added
- Market data module: Alpaca REST API for equity/crypto OHLCV bars with yfinance fallback, pagination, rate limiting, incremental backfill, real-time quotes via fetch_latest_quote
- Fundamentals module: yfinance extraction of P/E, P/S, revenue growth, FCF margin, debt/equity with sector average computation and store-to-postgres upsert
- SEC filings module: edgartools Form 4 insider trade tracking with buy/sell/exercise classification and data quality logging
- Regime detector: rule-based market classifier (bull/bear/sideways/high_vol/insufficient_data) using rolling volatility, SMA50/200 cross, drawdown from high; separate equity (SPY) and crypto (BTC) classifiers
- Backfill script: orchestrates market_data → regimes → fundamentals → insider trades with independent error handling per step; supports --years and --symbols flags
- TODOS.md for tracking deferred work items from eng review
- 75 new tests (173 total) across 4 test files covering all Phase 2 modules

### Fixed
- db.py: lazy asyncio.Lock init to avoid event loop errors at import time
- resilience.py: circuit breaker now uses asyncio.Lock instead of threading.Lock for async safety
- symbols.py: GOOG → GOOGL mapping (Google trades as GOOGL on exchanges)
- validators.py: added validate_ohlcv alias for market_data.py import compatibility
- 001_initial.sql: removed duplicate url column in data_quality table
- market_data.py: _parse_bar returns None on unparseable timestamps instead of silently using today's date
- regime_detector.py: UPDATE query now filters by market type (equity vs crypto) to prevent cross-contamination
- backfill.py: fixed missing years parameter in _run_steps, added try/finally for clean pool shutdown

## [0.1.0.0] - 2026-03-23

### Added
- Database layer with asyncpg connection pooling and migration runner for Neon PostgreSQL
- Symbol normalization module for internal ↔ Alpaca format conversion
- Retry decorator with circuit breaker pattern (closed → open → half-open states)
- Data validation with bounds checking and OHLC cross-validation
- Initial SQL migration with schema for market data, fundamentals, trades, and signals
- Dual-track risk configuration: fundamental-gated (8% max) and momentum-only (4% max)
- Comprehensive test suite (82 tests) covering all foundation modules

### Changed
- Updated dependencies: asyncpg replaces aiosqlite, added structlog and exchange_calendars
- Updated .gitignore to anchor `/data/` pattern to project root only
- Updated evolution constraints and CLAUDE.md for dual-track strategy system
