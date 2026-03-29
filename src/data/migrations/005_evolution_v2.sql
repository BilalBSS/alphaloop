-- / hierarchical evolution: sector profiles + symbol profiles

CREATE TABLE IF NOT EXISTS sector_profiles (
    id BIGSERIAL PRIMARY KEY,
    sector VARCHAR(30) NOT NULL,
    date DATE NOT NULL,
    best_indicators JSONB,
    best_fundamentals JSONB,
    avg_sharpe DECIMAL(8,4),
    avg_win_rate DECIMAL(5,4),
    total_trades INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(sector, date)
);
CREATE INDEX IF NOT EXISTS idx_sector_profiles_sector ON sector_profiles(sector, date);

CREATE TABLE IF NOT EXISTS symbol_profiles (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    sector VARCHAR(30),
    date DATE NOT NULL,
    tier VARCHAR(10) DEFAULT 'sector',
    parameter_overrides JSONB,
    avg_sharpe DECIMAL(8,4),
    total_trades INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_symbol_profiles_symbol ON symbol_profiles(symbol, date);
