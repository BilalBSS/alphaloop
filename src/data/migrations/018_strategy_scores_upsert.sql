-- / unique constraint for strategy_scores upsert (live metrics + evolution)
ALTER TABLE strategy_scores
    ADD CONSTRAINT uq_strategy_scores_period
    UNIQUE (strategy_id, period_start, period_end);
