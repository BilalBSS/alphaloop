CREATE TABLE IF NOT EXISTS earnings_revisions (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    period VARCHAR(10) NOT NULL,
    estimate_date DATE NOT NULL,
    eps_estimate DECIMAL(10, 4),
    revenue_estimate DECIMAL(16, 2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, period, estimate_date)
);
CREATE INDEX IF NOT EXISTS idx_revisions_symbol ON earnings_revisions(symbol, period);
