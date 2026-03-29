-- / computed technical indicators for dashboard transparency
CREATE TABLE IF NOT EXISTS computed_indicators (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    rsi14 DECIMAL(5,2),
    macd DECIMAL(12,4),
    macd_signal DECIMAL(12,4),
    macd_histogram DECIMAL(12,4),
    adx DECIMAL(5,2),
    sma20 DECIMAL(12,4),
    sma50 DECIMAL(12,4),
    bb_upper DECIMAL(12,4),
    bb_middle DECIMAL(12,4),
    bb_lower DECIMAL(12,4),
    atr DECIMAL(12,4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_computed_indicators_symbol_date
    ON computed_indicators(symbol, date DESC);
