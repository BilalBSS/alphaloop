CREATE TABLE IF NOT EXISTS system_flags (
    key   TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO system_flags (key, value)
VALUES ('executor_paused', 'false'::jsonb)
ON CONFLICT (key) DO NOTHING;
