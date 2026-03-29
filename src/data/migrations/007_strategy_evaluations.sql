-- / strategy evaluation cycle stats for observability dashboard
CREATE TABLE IF NOT EXISTS strategy_evaluations (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_pairs INTEGER NOT NULL DEFAULT 0,
    entry_hits INTEGER NOT NULL DEFAULT 0,
    blocked_consensus INTEGER NOT NULL DEFAULT 0,
    blocked_threshold INTEGER NOT NULL DEFAULT 0,
    signals_generated INTEGER NOT NULL DEFAULT 0,
    strategies_evaluated INTEGER NOT NULL DEFAULT 0,
    near_misses JSONB DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_created
    ON strategy_evaluations(created_at DESC);
