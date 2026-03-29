-- 002_system_events.sql
-- event log for health monitoring + error tracking

CREATE TABLE IF NOT EXISTS system_events (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    level VARCHAR(10) NOT NULL,
    source VARCHAR(50) NOT NULL,
    symbol VARCHAR(20),
    message TEXT NOT NULL,
    details JSONB
);
CREATE INDEX IF NOT EXISTS idx_system_events_ts ON system_events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_system_events_source ON system_events(source);
