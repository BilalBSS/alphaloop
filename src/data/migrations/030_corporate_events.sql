CREATE TABLE IF NOT EXISTS corporate_events (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    event_type VARCHAR(30) NOT NULL,
    event_date DATE NOT NULL,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, event_type, event_date)
);
CREATE INDEX IF NOT EXISTS idx_events_symbol ON corporate_events(symbol, event_date);
