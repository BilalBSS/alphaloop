-- / intraday bars table for 2h (and future) timeframes
-- / separate from market_data to avoid breaking the daily pipeline
CREATE TABLE IF NOT EXISTS market_data_intraday (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    timeframe VARCHAR(10) NOT NULL DEFAULT '2Hour',
    open DECIMAL(12,4),
    high DECIMAL(12,4),
    low DECIMAL(12,4),
    close DECIMAL(12,4),
    volume BIGINT,
    vwap DECIMAL(12,4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, timestamp, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_intraday_symbol_ts ON market_data_intraday(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_intraday_timeframe ON market_data_intraday(timeframe);

-- / add timeframe column to computed_indicators for 2h indicator storage
ALTER TABLE computed_indicators ADD COLUMN IF NOT EXISTS timeframe VARCHAR(10) DEFAULT '1Day';

-- / update unique constraint to include timeframe (drop old, add new)
-- / wrap in DO block so it doesn't fail if constraint doesn't exist
DO $$
BEGIN
    ALTER TABLE computed_indicators DROP CONSTRAINT IF EXISTS computed_indicators_symbol_date_key;
    ALTER TABLE computed_indicators ADD CONSTRAINT computed_indicators_symbol_date_tf_key UNIQUE (symbol, date, timeframe);
EXCEPTION WHEN OTHERS THEN
    NULL;
END $$;
