# Changelog

All notable changes to this project will be documented in this file.

## [0.5.0.0] - 2026-03-25

### Added
- Strategy framework (Phase 5): 4 modules in src/strategies/
- base_strategy.py: abstract StrategyInterface + ConfigDrivenStrategy that evaluates JSON configs against 8 indicator types (bollinger, rsi, macd, volume, sma, adx, atr, stochastic) + 7 fundamental filters (pe, pe_vs_sector, revenue_growth, fcf_margin, debt_to_equity, dcf_upside, insider_buying_recent)
- strategy_loader.py: Pydantic-validated JSON config loader with dual-track constraints (fundamental-gated: max 8% position, ≥2 signals; momentum-only: max 4% position, ≥1 signal), config save/load for evolution engine with path sanitization
- strategy_pool.py: manages N concurrent strategies with composite scoring (sharpe * 0.4 + win_rate * 0.3 - |max_drawdown| * 0.2 + (0.25 - brier) * 0.1), ranked views, bottom quartile detection, lifecycle tracking (backtest_pending → paper_trading → live → killed)
- backtest.py: backtesting engine with anti-lookahead (signal evaluated at previous bar close, filled at next bar open), computes Sharpe, Sortino, Calmar, max drawdown, win rate, profit factor, avg holding days; simulates through PaperBroker for realistic fills
- 10 seed strategy configs: 4 fundamental-gated (bollinger PE oversold, RSI deep value, stochastic FCF mean reversion, DCF undervalued accumulator) + 6 momentum-only (MACD breakout, keltner breakout, volume surge, SMA golden cross, ADX trend rider, bollinger squeeze breakout)
- universe resolution system: strategies reference "all", "all_stocks", "all_crypto" instead of hardcoded tickers, resolved at runtime from database
- 282 new tests (648 total) across 4 test files covering all Phase 5 modules

### Fixed
- backtest.py: sortino ratio now returns inf for all-positive returns (was incorrectly 0)
- backtest.py: calmar ratio uses compound annualization instead of linear extrapolation
- base_strategy.py: ATR trailing stop now scopes highest-since-entry from entry date, not all history
- base_strategy.py: ATR entry signal now supports above/below conditions with threshold (was always-true)
- base_strategy.py: fundamental filters reject when data is None instead of silently passing
- base_strategy.py: insider_buying_recent filter now implemented (was declared but never checked)
- base_strategy.py: removed dead fixed_pct take profit code path
- strategy_loader.py: load_config_file passes pydantic-normalized dict to ConfigDrivenStrategy (was passing raw dict, bypassing coercions)
- strategy_loader.py: save_config validates before writing and sanitizes strategy_id (prevents path traversal)
- strategy_loader.py: empty fundamental_filters {} no longer bypasses momentum-only position cap
- strategy_loader.py: narrowed exception handling from catch-all to specific types
- symbols.py: sp500/nasdaq100 raise NotImplementedError instead of silently resolving to wrong universe

## [0.4.0.0] - 2026-03-25

### Added
- Technical indicators (Phase 4): 4 modules in src/indicators/
- trend.py: SMA, EMA, MACD (with histogram), ADX (Wilder smoothing), true_range, Supertrend (with direction tracking)
- momentum.py: RSI (Wilder smoothing), Stochastic (%K/%D), CCI, Williams %R, ROC
- volatility.py: Bollinger Bands (with bandwidth + %B), ATR (Wilder smoothing), Keltner Channel
- volume.py: OBV, VWAP, Volume Profile (POC + value area), MFI
- Broker layer (Phase 4): 4 modules in src/brokers/
- base.py: abstract BrokerInterface ABC with Order/Position/AccountBalance dataclasses
- paper_broker.py: simulated broker with instant fills, limit orders, position tracking, avg price recalc
- alpaca_broker.py: REST API broker for stocks + crypto with retry decorator
- broker_factory.py: routes all symbols to paper or live broker by mode
- 97 new tests (366 total) across 7 test files covering all Phase 4 modules

### Fixed
- paper_broker.py: replaced deprecated datetime.utcnow() with datetime.now(timezone.utc)
- paper_broker.py: use full uuid for order ids (truncated 8-char had birthday-paradox collision risk in backtesting)
- paper_broker.py: limit orders now fill at better price (min of market/limit for buys, max for sells)
- paper_broker.py: float tolerance for position cleanup (abs < 1e-9 instead of == 0)
- paper_broker.py: reject unsupported order types (stop/stop_limit) with clear error instead of silently treating as market
- alpaca_broker.py: get_price raises ValueError on missing/zero price instead of silently returning 0.0
- alpaca_broker.py: removed @with_retry from place_order to prevent duplicate real-money orders on transient failures
- broker_factory.py: validate mode in __init__, raise ValueError for unknown modes

## [0.3.0.0] - 2026-03-25

### Added
- Analysis engine (Phase 3): 6 modules in src/analysis/
- ratio_analysis.py: P/E, P/S, PEG, FCF margin, D/E scoring (0-100) with sector comparison and weighted composite
- dcf_model.py: Monte Carlo DCF (10k simulations) with randomized growth, margins, terminal multiples; outputs p10/median/p90 fair value distribution
- sensitivity.py: growth rate x terminal multiple sensitivity grid with driver detection (which assumption matters most)
- earnings_signals.py: yfinance earnings surprise detection, consecutive beat/miss streaks, bullish/bearish signal scoring
- insider_activity.py: insider trade aggregation from DB, title-weighted net buy ratio (CEO 3x, CFO 2.5x, Director 1x), cluster detection (3+ insiders buying within 30 days)
- ai_summary.py: Groq free tier LLM summary with structured fallback when API unavailable
- 96 new tests (269 total) across 6 test files covering all Phase 3 modules
- Quant engine mathematical foundations documented in CLAUDE.md (variance reduction, importance sampling, particle filter, copulas)

### Fixed
- CLAUDE.md: synchronized phases, build order, architecture tree, and test counts across all sections
- dcf_model.py: clamp growth rates to [-0.5, 1.0] to prevent negative revenue in MC simulation
- dcf_model.py: json.dumps assumptions dict for asyncpg jsonb compatibility
- ai_summary.py: sanitize groq exception logs to prevent api key leak, add dcf to fallback confidence
- earnings_signals.py: sort quarters most-recent-first (yfinance order not guaranteed), clamp surprise_pct to [-5, 5]
- ratio_analysis.py: removed wrong d/e > 10 normalization heuristic (yfinance returns ratio not percentage)

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
