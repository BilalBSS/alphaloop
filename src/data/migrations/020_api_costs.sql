CREATE TABLE IF NOT EXISTS api_costs (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    source VARCHAR(50) NOT NULL,
    call_count INTEGER DEFAULT 0,
    tokens_in BIGINT DEFAULT 0,
    tokens_out BIGINT DEFAULT 0,
    estimated_cost_usd DECIMAL(10, 6) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(date, source)
);
CREATE INDEX IF NOT EXISTS idx_api_costs_date ON api_costs(date);
