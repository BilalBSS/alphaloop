CREATE TABLE IF NOT EXISTS options_data (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    iv_current DECIMAL(8, 4),
    iv_rank DECIMAL(5, 4),
    put_call_ratio DECIMAL(8, 4),
    max_pain DECIMAL(10, 2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_options_symbol ON options_data(symbol, date);
