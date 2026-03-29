-- / daily reasoner synthesis — 5PM ET portfolio-wide analysis

CREATE TABLE IF NOT EXISTS daily_synthesis (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    model VARCHAR(30) NOT NULL,
    top_buys JSONB,
    top_avoids JSONB,
    portfolio_risk TEXT,
    per_symbol_notes JSONB,
    raw_response TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_daily_synthesis_date ON daily_synthesis(date);
