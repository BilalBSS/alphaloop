-- / add sortino_ratio and composite_score to strategy_scores
ALTER TABLE strategy_scores ADD COLUMN IF NOT EXISTS sortino_ratio DECIMAL(8,4);
ALTER TABLE strategy_scores ADD COLUMN IF NOT EXISTS composite_score DECIMAL(8,4);
