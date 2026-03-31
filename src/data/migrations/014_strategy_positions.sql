CREATE TABLE IF NOT EXISTS strategy_positions (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    qty DECIMAL(14,4) NOT NULL DEFAULT 0,
    avg_entry_price DECIMAL(12,4),
    opened_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(strategy_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_strategy_positions_symbol ON strategy_positions(symbol);
