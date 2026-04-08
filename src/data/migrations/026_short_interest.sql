CREATE TABLE IF NOT EXISTS short_interest (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    short_volume BIGINT,
    total_volume BIGINT,
    short_ratio DECIMAL(8, 4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_short_interest_symbol ON short_interest(symbol, date);
