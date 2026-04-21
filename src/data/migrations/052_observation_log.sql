-- observation_log: near-miss trail for entry-condition evaluation. when a
-- strategy evaluates a (symbol, cycle) pair and N-1 of N AND clauses pass
-- (or one OR branch passes but strength sub-threshold), we log it here.
-- kept separate from trade_signals so the evolution engine does not train
-- on forced / sub-threshold signals. surfaced on the analysis tab as "close
-- to firing" so the dashboard lights up with honest diagnostic activity.

CREATE TABLE IF NOT EXISTS observation_log (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    -- near_miss_type: 'n_minus_1_technical' | 'fundamental_gate' | 'consensus_block' | 'threshold_block'
    near_miss_type VARCHAR(40) NOT NULL,
    passed_count INT,
    total_count INT,
    strength NUMERIC(5, 3),
    failed_reason TEXT,
    regime VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_observation_log_strategy_created
    ON observation_log(strategy_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_observation_log_symbol_created
    ON observation_log(symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_observation_log_created
    ON observation_log(created_at DESC);
