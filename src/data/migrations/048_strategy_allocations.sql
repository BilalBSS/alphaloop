-- / phase 6 step 10: kelly-weighted capital allocation per strategy
-- / capital_allocator writes weekly; risk_agent reads per-signal to size positions

CREATE TABLE IF NOT EXISTS strategy_allocations (
    strategy_id      VARCHAR(64) PRIMARY KEY,
    kelly_fraction   DOUBLE PRECISION NOT NULL,         -- raw kelly from strategy config
    rank_weight      DOUBLE PRECISION NOT NULL,         -- 2.0 top-quartile, 1.0 middle, 0.5 bottom
    allocated_weight DOUBLE PRECISION NOT NULL,         -- kelly × rank × cap clamp
    composite_score  DOUBLE PRECISION,                  -- what drove the ranking
    trade_count      INTEGER NOT NULL DEFAULT 0,        -- must be >=30 before kelly engages
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strategy_allocations_weight
    ON strategy_allocations (allocated_weight DESC);
