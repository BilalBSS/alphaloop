-- / add broker order_id to approved_trades so sync_trades_from_alpaca can
-- / recover strategy_id attribution on reconciled fills.
-- / index supports the point lookup in sync without a full scan.

ALTER TABLE approved_trades ADD COLUMN IF NOT EXISTS order_id VARCHAR(100);

CREATE INDEX IF NOT EXISTS idx_approved_trades_order_id
    ON approved_trades(order_id)
    WHERE order_id IS NOT NULL;
