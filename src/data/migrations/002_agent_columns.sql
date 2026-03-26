-- 002_agent_columns.sql
-- add status tracking for agent pipeline polling

-- trade_signals needs status for risk agent polling
ALTER TABLE trade_signals ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'pending';
CREATE INDEX IF NOT EXISTS idx_trade_signals_status ON trade_signals(status);

-- traceability: link approved_trades and trade_log back to strategy
ALTER TABLE approved_trades ADD COLUMN IF NOT EXISTS strategy_id VARCHAR(50);
ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS strategy_id VARCHAR(50);
