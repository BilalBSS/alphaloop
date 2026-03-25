-- 001_initial.sql
-- Creates all tables for the data layer + analysis engine.
-- Includes regime tagging, dual-track support, and data quality tracking.

-- Core market data with regime tagging
CREATE TABLE IF NOT EXISTS market_data (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    open DECIMAL(12,4),
    high DECIMAL(12,4),
    low DECIMAL(12,4),
    close DECIMAL(12,4),
    volume BIGINT,
    vwap DECIMAL(12,4),
    regime VARCHAR(20),             -- bull, bear, sideways, high_vol, insufficient_data
    regime_confidence DECIMAL(4,3), -- 0.000 to 1.000
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_market_data_symbol_date ON market_data(symbol, date);
CREATE INDEX IF NOT EXISTS idx_market_data_regime ON market_data(regime);

-- Fundamental snapshots
CREATE TABLE IF NOT EXISTS fundamentals (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    pe_ratio DECIMAL(10,2),
    pe_forward DECIMAL(10,2),
    ps_ratio DECIMAL(10,2),
    peg_ratio DECIMAL(10,2),
    revenue_growth_1y DECIMAL(8,4),
    revenue_growth_3y DECIMAL(8,4),
    fcf_margin DECIMAL(8,4),
    debt_to_equity DECIMAL(10,2),
    sector VARCHAR(50),
    sector_pe_avg DECIMAL(10,2),
    sector_ps_avg DECIMAL(10,2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol_date ON fundamentals(symbol, date);

-- DCF valuation results (Monte Carlo distribution)
CREATE TABLE IF NOT EXISTS dcf_valuations (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    fair_value_median DECIMAL(12,2),
    fair_value_p10 DECIMAL(12,2),
    fair_value_p90 DECIMAL(12,2),
    current_price DECIMAL(12,2),
    upside_pct DECIMAL(8,4),
    num_simulations INT DEFAULT 10000,
    dcf_confidence VARCHAR(10),      -- high, medium, low
    assumptions JSONB,
    regime VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_dcf_valuations_symbol_date ON dcf_valuations(symbol, date);

-- News sentiment (persisted for historical analysis)
CREATE TABLE IF NOT EXISTS news_sentiment (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    headline TEXT,
    source VARCHAR(100),
    sentiment_score DECIMAL(4,3),     -- -1.000 to 1.000
    sentiment_label VARCHAR(20),      -- positive, negative, neutral
    url TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_sentiment_dedup ON news_sentiment(symbol, date, url);
CREATE INDEX IF NOT EXISTS idx_news_sentiment_symbol_date ON news_sentiment(symbol, date);

-- Analysis scores (analyst agent writes, strategy agent reads)
CREATE TABLE IF NOT EXISTS analysis_scores (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    fundamental_score DECIMAL(5,2),   -- 0-100
    technical_score DECIMAL(5,2),     -- 0-100 (null for fundamental-only)
    composite_score DECIMAL(5,2),     -- weighted combination
    regime VARCHAR(20),
    regime_confidence DECIMAL(4,3),
    used_fundamentals BOOLEAN,        -- descriptive: did THIS analysis use fundamentals?
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_analysis_scores_symbol_date ON analysis_scores(symbol, date);

-- Insider trading activity (SEC Form 4)
CREATE TABLE IF NOT EXISTS insider_trades (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    filing_date DATE NOT NULL,
    insider_name VARCHAR(200),
    insider_title VARCHAR(100),
    transaction_type VARCHAR(20),     -- buy, sell, option_exercise
    shares DECIMAL(14,4),             -- supports fractional shares (RSU vesting)
    price_per_share DECIMAL(12,4),
    total_value DECIMAL(14,2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, filing_date, insider_name, transaction_type, shares, price_per_share)
);
CREATE INDEX IF NOT EXISTS idx_insider_trades_symbol ON insider_trades(symbol, filing_date);

-- Market regime history (separate rows for equity and crypto)
CREATE TABLE IF NOT EXISTS regime_history (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    market VARCHAR(10) NOT NULL DEFAULT 'equity',  -- equity, crypto
    regime VARCHAR(20) NOT NULL,      -- bull, bear, sideways, high_vol, insufficient_data
    confidence DECIMAL(4,3),
    volatility_20d DECIMAL(8,4),
    trend_sma50_above_200 BOOLEAN,
    drawdown_from_high DECIMAL(8,4),
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(date, market)
);
CREATE INDEX IF NOT EXISTS idx_regime_history_date ON regime_history(date, market);

-- Data quality tracking
CREATE TABLE IF NOT EXISTS data_quality (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(50) NOT NULL,      -- market_data, fundamentals, sec_filings, etc.
    symbol VARCHAR(20),
    date DATE,
    issue_type VARCHAR(30),           -- gap, stale, partial, api_error, validation_failed
    details TEXT,
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_data_quality_unresolved ON data_quality(resolved, source);

-- Trade signals (strategy agent writes, risk agent reads) — used in later phases
CREATE TABLE IF NOT EXISTS trade_signals (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    signal_type VARCHAR(10) NOT NULL, -- buy, sell
    strength DECIMAL(5,2),
    regime VARCHAR(20),
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trade_signals_symbol_created ON trade_signals(symbol, created_at);

-- Approved trades (risk agent writes, executor reads) — used in later phases
CREATE TABLE IF NOT EXISTS approved_trades (
    id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT REFERENCES trade_signals(id),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    qty DECIMAL(14,4),
    order_type VARCHAR(20) DEFAULT 'market',
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Trade log (executor writes, all agents + evolution engine reads) — used in later phases
CREATE TABLE IF NOT EXISTS trade_log (
    id BIGSERIAL PRIMARY KEY,
    trade_id BIGINT REFERENCES approved_trades(id),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    qty DECIMAL(14,4),
    price DECIMAL(12,4),
    order_id VARCHAR(100),
    broker VARCHAR(20),
    regime VARCHAR(20),
    pnl DECIMAL(14,2),
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trade_log_symbol ON trade_log(symbol, created_at);

-- Strategy scores (backtester writes, evolution engine reads) — used in later phases
CREATE TABLE IF NOT EXISTS strategy_scores (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(50) NOT NULL,
    period_start DATE,
    period_end DATE,
    sharpe_ratio DECIMAL(8,4),
    max_drawdown DECIMAL(8,4),
    win_rate DECIMAL(5,4),
    brier_score DECIMAL(5,4),
    total_trades INT,
    regime_breakdown JSONB,          -- per-regime performance
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Evolution log — used in later phases
CREATE TABLE IF NOT EXISTS evolution_log (
    id BIGSERIAL PRIMARY KEY,
    generation INT NOT NULL,
    action VARCHAR(20) NOT NULL,     -- kill, mutate, promote, demote
    strategy_id VARCHAR(50) NOT NULL,
    parent_id VARCHAR(50),
    reason TEXT,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
