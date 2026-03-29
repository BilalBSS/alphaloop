-- 003_phase8_tables.sql
-- crypto on-chain data + notification log

CREATE TABLE IF NOT EXISTS crypto_onchain (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    data_type VARCHAR(50) NOT NULL,
    chain VARCHAR(30) DEFAULT 'ethereum',
    data JSONB NOT NULL DEFAULT '{}',
    source VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_crypto_onchain_symbol_date ON crypto_onchain(symbol, date);
CREATE INDEX IF NOT EXISTS idx_crypto_onchain_type ON crypto_onchain(data_type);

CREATE TABLE IF NOT EXISTS notification_log (
    id SERIAL PRIMARY KEY,
    severity VARCHAR(20) NOT NULL,
    title VARCHAR(200) NOT NULL,
    channel VARCHAR(20) NOT NULL,
    success BOOLEAN NOT NULL,
    error TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notification_log_created ON notification_log(created_at);
