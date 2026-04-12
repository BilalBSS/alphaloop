-- wiki_documents: metadata index for trading-wiki/ markdown files
CREATE TABLE IF NOT EXISTS wiki_documents (
    id BIGSERIAL PRIMARY KEY,
    path VARCHAR(500) NOT NULL UNIQUE,
    category VARCHAR(40) NOT NULL,
    title VARCHAR(300),
    symbols TEXT[] NOT NULL DEFAULT '{}',
    strategy_ids TEXT[] NOT NULL DEFAULT '{}',
    word_count INT NOT NULL DEFAULT 0,
    confidence VARCHAR(20) NOT NULL DEFAULT 'emerging',
    content_tsv tsvector,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wiki_docs_category ON wiki_documents(category);
CREATE INDEX IF NOT EXISTS idx_wiki_docs_symbols ON wiki_documents USING GIN(symbols);
CREATE INDEX IF NOT EXISTS idx_wiki_docs_strategies ON wiki_documents USING GIN(strategy_ids);
CREATE INDEX IF NOT EXISTS idx_wiki_docs_tsv ON wiki_documents USING GIN(content_tsv);
CREATE INDEX IF NOT EXISTS idx_wiki_docs_updated ON wiki_documents(updated_at DESC);

-- post_mortems: structured data behind post-mortem markdown docs
CREATE TABLE IF NOT EXISTS post_mortems (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    trigger_type VARCHAR(30) NOT NULL,
    pnl DECIMAL(14, 2),
    expected_pnl DECIMAL(14, 2),
    deviation_sigma DECIMAL(8, 4),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    wiki_path VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_post_mortems_strategy ON post_mortems(strategy_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_post_mortems_symbol ON post_mortems(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_post_mortems_trigger ON post_mortems(trigger_type);

-- regime_shifts: structured record of regime transitions with wiki link
CREATE TABLE IF NOT EXISTS regime_shifts (
    id BIGSERIAL PRIMARY KEY,
    old_regime VARCHAR(20) NOT NULL,
    new_regime VARCHAR(20) NOT NULL,
    market VARCHAR(10) NOT NULL DEFAULT 'equity',
    confidence DECIMAL(4, 3),
    wiki_path VARCHAR(500),
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_regime_shifts_market ON regime_shifts(market, detected_at DESC);

-- strategy_lessons: per-strategy accumulated lessons (append-only)
CREATE TABLE IF NOT EXISTS strategy_lessons (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(50) NOT NULL,
    lesson_type VARCHAR(40) NOT NULL,
    content TEXT NOT NULL,
    context JSONB,
    confidence VARCHAR(20) NOT NULL DEFAULT 'emerging',
    trade_count INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_strategy_lessons_sid ON strategy_lessons(strategy_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_lessons_type ON strategy_lessons(lesson_type);

-- evolution_mutations: tracks every mutation attempt with wiki_guided flag for A/B
CREATE TABLE IF NOT EXISTS evolution_mutations (
    id BIGSERIAL PRIMARY KEY,
    generation INT NOT NULL,
    parent_strategy_id VARCHAR(50) NOT NULL,
    mutant_strategy_id VARCHAR(50),
    wiki_guided BOOLEAN NOT NULL DEFAULT FALSE,
    wiki_context_tokens INT,
    mutation_diff JSONB,
    parent_sharpe DECIMAL(8, 4),
    mutant_sharpe DECIMAL(8, 4),
    sharpe_delta DECIMAL(8, 4),
    survived BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_evolution_mutations_gen ON evolution_mutations(generation, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_evolution_mutations_wg ON evolution_mutations(wiki_guided, survived);
