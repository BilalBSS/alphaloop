CREATE TABLE IF NOT EXISTS earnings_transcripts (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    quarter VARCHAR(10) NOT NULL,
    transcript TEXT,
    sentiment_score DECIMAL(5, 4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, quarter)
);
CREATE INDEX IF NOT EXISTS idx_transcripts_symbol ON earnings_transcripts(symbol);
