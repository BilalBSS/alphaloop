CREATE TABLE IF NOT EXISTS crypto_liquidations (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    long_liquidations DECIMAL(16, 2),
    short_liquidations DECIMAL(16, 2),
    liquidation_imbalance DECIMAL(5, 4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_liquidations_symbol ON crypto_liquidations(symbol, date);
