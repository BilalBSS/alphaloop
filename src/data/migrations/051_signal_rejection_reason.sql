-- capture why a trade_signal was rejected by the risk agent so the dashboard
-- can explain "signals generated but no trades". previously risk_agent only
-- flipped status to 'rejected' without persisting the reason, so the signal
-- -> trade funnel was opaque.

ALTER TABLE trade_signals
    ADD COLUMN IF NOT EXISTS rejection_reason VARCHAR(80);

CREATE INDEX IF NOT EXISTS idx_trade_signals_status_reason
    ON trade_signals(status, rejection_reason)
    WHERE status = 'rejected';
