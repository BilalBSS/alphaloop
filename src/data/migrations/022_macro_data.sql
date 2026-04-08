CREATE TABLE IF NOT EXISTS macro_data (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    series_id VARCHAR(20) NOT NULL,
    value DECIMAL(12, 4),
    normalized DECIMAL(5, 4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(date, series_id)
);
CREATE INDEX IF NOT EXISTS idx_macro_data_series ON macro_data(series_id, date);
