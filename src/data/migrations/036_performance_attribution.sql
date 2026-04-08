CREATE TABLE IF NOT EXISTS performance_attribution (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(50) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    total_return DECIMAL(14, 4),
    market_contribution DECIMAL(14, 4),
    sector_contribution DECIMAL(14, 4),
    stock_contribution DECIMAL(14, 4),
    alpha DECIMAL(14, 4),
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(strategy_id, period_start, period_end)
);
