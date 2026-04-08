CREATE TABLE IF NOT EXISTS institutional_holdings (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    filing_date DATE NOT NULL,
    institution_count INTEGER,
    total_shares BIGINT,
    shares_change BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, filing_date)
);
CREATE INDEX IF NOT EXISTS idx_institutional_symbol ON institutional_holdings(symbol, filing_date);
