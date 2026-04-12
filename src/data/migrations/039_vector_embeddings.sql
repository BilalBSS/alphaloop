-- requires: CREATE EXTENSION IF NOT EXISTS vector;
-- run manually on VPS postgres before this migration:
--   sudo -u postgres psql -d alphaloop -c "CREATE EXTENSION IF NOT EXISTS vector;"
CREATE EXTENSION IF NOT EXISTS vector;

-- wiki_embeddings: 768-dim vectors over wiki document chunks (nomic-embed-text)
CREATE TABLE IF NOT EXISTS wiki_embeddings (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES wiki_documents(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_wiki_embeddings_doc ON wiki_embeddings(document_id);
CREATE INDEX IF NOT EXISTS idx_wiki_embeddings_hnsw
    ON wiki_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- chart_analyses: gemini vision output + embeddings for chart images
CREATE TABLE IF NOT EXISTS chart_analyses (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL DEFAULT '1Day',
    image_path VARCHAR(500),
    analysis_text TEXT NOT NULL,
    patterns_detected JSONB NOT NULL DEFAULT '[]'::jsonb,
    trend VARCHAR(20),
    support_levels DECIMAL(12, 4)[],
    resistance_levels DECIMAL(12, 4)[],
    bullish_score DECIMAL(4, 2),
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chart_analyses_symbol ON chart_analyses(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chart_analyses_created ON chart_analyses(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chart_analyses_hnsw
    ON chart_analyses USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
