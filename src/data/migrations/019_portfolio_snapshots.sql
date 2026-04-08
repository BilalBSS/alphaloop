CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    equity DECIMAL(14, 2),
    cash DECIMAL(14, 2),
    positions_value DECIMAL(14, 2),
    drawdown_from_peak DECIMAL(8, 6),
    peak_equity DECIMAL(14, 2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(date)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_date ON portfolio_snapshots(date);
