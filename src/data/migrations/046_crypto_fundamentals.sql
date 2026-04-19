-- phase 5 step 6: cache table for crypto fundamentals
-- one row per (symbol, date). upsert on conflict. fields nullable so a single
-- source failure still lets the rest populate.
CREATE TABLE IF NOT EXISTS crypto_fundamentals (
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    nvt_ratio DECIMAL(14, 4),
    funding_rate DECIMAL(10, 6),
    active_addresses BIGINT,
    exchange_inflow_usd DECIMAL(20, 2),
    hash_rate DECIMAL(20, 4),
    tvl_usd DECIMAL(20, 2),
    dex_volume_24h DECIMAL(20, 2),
    stablecoin_supply_ratio DECIMAL(10, 6),
    sources JSONB DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_crypto_fundamentals_updated ON crypto_fundamentals(updated_at DESC);
