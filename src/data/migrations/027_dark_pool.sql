CREATE TABLE IF NOT EXISTS dark_pool (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    week_start DATE NOT NULL,
    ats_volume BIGINT,
    total_volume BIGINT,
    dark_pool_ratio DECIMAL(8, 4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, week_start)
);
CREATE INDEX IF NOT EXISTS idx_dark_pool_symbol ON dark_pool(symbol, week_start);
