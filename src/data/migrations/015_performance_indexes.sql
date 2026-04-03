-- trade_log.order_id (n+1 sync runs every 5 min)
CREATE INDEX IF NOT EXISTS idx_trade_log_order_id
    ON trade_log(order_id);

-- approved_trades status + created_at (polled every 5 sec by executor)
CREATE INDEX IF NOT EXISTS idx_approved_trades_status_created
    ON approved_trades(status, created_at);

-- trade_signals composite for dedup (hundreds/hour)
CREATE INDEX IF NOT EXISTS idx_trade_signals_dedup
    ON trade_signals(strategy_id, symbol, signal_type, created_at)
    WHERE status = 'pending';

-- trade_signals status+created for pending poll (every 5 sec)
CREATE INDEX IF NOT EXISTS idx_trade_signals_pending
    ON trade_signals(status, created_at)
    WHERE status = 'pending';

-- trade_log.strategy_id for evolution + dashboard aggregation
CREATE INDEX IF NOT EXISTS idx_trade_log_strategy_id
    ON trade_log(strategy_id, symbol);

-- strategy_scores.strategy_id for lookups
CREATE INDEX IF NOT EXISTS idx_strategy_scores_strategy
    ON strategy_scores(strategy_id, created_at DESC);

-- system_events level+timestamp for health endpoint
CREATE INDEX IF NOT EXISTS idx_system_events_level_ts
    ON system_events(level, timestamp DESC);

-- evolution_log created_at for dashboard sort
CREATE INDEX IF NOT EXISTS idx_evolution_log_created
    ON evolution_log(created_at DESC);
