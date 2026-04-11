-- user_chart_drawings: user-placed drawings (lines, fibs, boxes, text)
CREATE TABLE IF NOT EXISTS user_chart_drawings (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    drawing_type VARCHAR(40) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chart_drawings_symbol ON user_chart_drawings(symbol);

-- chart_alerts: price-cross alerts fired by alert engine in orchestrator
CREATE TABLE IF NOT EXISTS chart_alerts (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    price DECIMAL(12, 4) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    label VARCHAR(200),
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    last_check TIMESTAMPTZ,
    fired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chart_alerts_symbol_status ON chart_alerts(symbol, status);

-- user_chart_state: per-symbol selected timeframe + active indicators + params
CREATE TABLE IF NOT EXISTS user_chart_state (
    symbol VARCHAR(20) PRIMARY KEY,
    timeframe VARCHAR(10) NOT NULL DEFAULT '1Hour',
    active_indicators JSONB NOT NULL DEFAULT '[]'::jsonb,
    indicator_params JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ui_events: lightweight fire-and-forget dashboard telemetry
CREATE TABLE IF NOT EXISTS ui_events (
    id BIGSERIAL PRIMARY KEY,
    kind VARCHAR(40) NOT NULL,
    symbol VARCHAR(20),
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ui_events_created ON ui_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ui_events_kind ON ui_events(kind);
