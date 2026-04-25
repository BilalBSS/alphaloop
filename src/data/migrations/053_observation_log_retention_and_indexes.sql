CREATE INDEX IF NOT EXISTS idx_analysis_scores_symbol_created
    ON analysis_scores(symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_analysis_scores_created
    ON analysis_scores(created_at DESC);
