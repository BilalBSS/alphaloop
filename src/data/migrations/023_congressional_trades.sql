CREATE TABLE IF NOT EXISTS congressional_trades (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    filing_date DATE NOT NULL,
    name VARCHAR(200),
    transaction_type VARCHAR(20),
    amount_range VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, filing_date, name, transaction_type)
);
CREATE INDEX IF NOT EXISTS idx_congressional_symbol ON congressional_trades(symbol, filing_date);
