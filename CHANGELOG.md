# Changelog

All notable changes to this project will be documented in this file.

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
