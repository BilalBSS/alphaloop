-- latest_prices: one row per symbol for real-time price snapshots
-- / replaces the broken pattern of writing polling snapshots into market_data_intraday
CREATE TABLE IF NOT EXISTS latest_prices (
    symbol VARCHAR(20) PRIMARY KEY,
    price DECIMAL(14, 4) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_latest_prices_updated ON latest_prices(updated_at DESC);

-- purge fake snapshot rows written by the old _price_refresh_loop
-- / signature: volume=0 AND open=high=low=close (polling snapshots store last price 4x)
-- / real sparse bars with 0 volume legitimately exist for illiquid symbols but have OHLC range
DELETE FROM market_data_intraday
WHERE volume = 0
  AND open = high
  AND high = low
  AND low = close;

-- purge leading zero-equity rows in portfolio_snapshots
-- / the equity chart showed 632 leading zero points from before the first real trade
-- / these break equity visualizations and drawdown calcs
DELETE FROM portfolio_snapshots WHERE equity IS NULL OR equity = 0;
