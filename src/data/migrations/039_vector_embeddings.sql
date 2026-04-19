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

