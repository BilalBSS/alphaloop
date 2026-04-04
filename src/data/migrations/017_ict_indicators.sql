-- / ict smart money data stored alongside regular indicators
ALTER TABLE computed_indicators ADD COLUMN IF NOT EXISTS ict_data JSONB;
