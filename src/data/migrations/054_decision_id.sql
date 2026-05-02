ALTER TABLE trade_signals ADD COLUMN IF NOT EXISTS decision_id VARCHAR(26);
ALTER TABLE approved_trades ADD COLUMN IF NOT EXISTS decision_id VARCHAR(26);
ALTER TABLE approved_trades ADD COLUMN IF NOT EXISTS sizing_details JSONB;
ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS decision_id VARCHAR(26);
ALTER TABLE evolution_mutations ADD COLUMN IF NOT EXISTS retrieval_cycle_id VARCHAR(26);

CREATE INDEX IF NOT EXISTS idx_trade_signals_decision_id
    ON trade_signals(decision_id) WHERE decision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_approved_trades_decision_id
    ON approved_trades(decision_id) WHERE decision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trade_log_decision_id
    ON trade_log(decision_id) WHERE decision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_evolution_mutations_retrieval_cycle
    ON evolution_mutations(retrieval_cycle_id) WHERE retrieval_cycle_id IS NOT NULL;
