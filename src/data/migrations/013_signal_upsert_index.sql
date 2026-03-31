-- 013_signal_upsert_index.sql
-- unique partial index for signal dedup: one pending signal per strategy+symbol+type per day

CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_signals_dedup
    ON trade_signals (strategy_id, symbol, signal_type, (created_at::date))
    WHERE status = 'pending';
