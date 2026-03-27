-- 004_social_sentiment.sql
-- social sentiment data from stocktwits, fear & greed, reddit (future)

CREATE TABLE IF NOT EXISTS social_sentiment (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    source VARCHAR(30) NOT NULL,
    bullish_pct DECIMAL(5,4),
    bearish_pct DECIMAL(5,4),
    volume INT,
    raw_score DECIMAL(5,3),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_social_sentiment_dedup ON social_sentiment(symbol, date, source);
CREATE INDEX IF NOT EXISTS idx_social_sentiment_symbol_date ON social_sentiment(symbol, date);
