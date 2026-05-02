CREATE TABLE IF NOT EXISTS retrieval_logs (
    cycle_id      VARCHAR(26) PRIMARY KEY,
    parent_id     VARCHAR(50) NOT NULL,
    child_id      VARCHAR(50),
    prompt_tokens INT NOT NULL,
    retrieved     JSONB NOT NULL,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_retrieval_logs_parent ON retrieval_logs(parent_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_retrieval_logs_ts ON retrieval_logs(ts DESC);
