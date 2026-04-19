-- / phase 6 step 1: loop introspection + manual trigger queue
-- / orchestrator writes to loop_activity on every fire; dashboard reads it
-- / trigger_requests is a cross-process command queue for /api/admin/trigger

CREATE TABLE IF NOT EXISTS loop_activity (
    name             VARCHAR(64) PRIMARY KEY,
    kind             VARCHAR(16) NOT NULL,               -- 'interval' | 'cron'
    cadence_seconds  INTEGER,                             -- null for cron
    cron_hour_et     INTEGER,                             -- null for interval
    last_fire_ts     TIMESTAMPTZ,
    last_duration_ms INTEGER,
    last_status      VARCHAR(16),                         -- 'ok' | 'error' | 'running'
    last_error       TEXT,
    next_fire_ts     TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_loop_activity_updated ON loop_activity (updated_at DESC);

CREATE TABLE IF NOT EXISTS trigger_requests (
    id           BIGSERIAL PRIMARY KEY,
    service      VARCHAR(64) NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_at   TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    status       VARCHAR(16) NOT NULL DEFAULT 'pending', -- pending | running | done | error
    error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_trigger_requests_pending
    ON trigger_requests (service, requested_at)
    WHERE status = 'pending';
