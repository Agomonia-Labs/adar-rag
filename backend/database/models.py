# database/models.py
# Creates all tables + indexes on startup.  Safe to call on every boot (IF NOT EXISTS).

from database.connection import get_pool, EMBEDDING_DIM


CREATE_SCHEMA = f"""
-- ── Extensions ─────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Users ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           TEXT        UNIQUE NOT NULL,
    hashed_password TEXT        NOT NULL,
    full_name       TEXT        NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Documents ───────────────────────────────────────────────────────────────
-- Tracks every uploaded file; GCS paths are stored here.
-- status flow:  uploading → chunking → chunked → embedding → embedded | error
CREATE TABLE IF NOT EXISTS documents (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename        TEXT        NOT NULL,
    original_name   TEXT        NOT NULL,
    file_type       TEXT        NOT NULL,
    file_size       BIGINT      NOT NULL,
    gcs_source_path TEXT        NOT NULL,   -- e.g. users/uid/documents/did/source/file.pdf
    gcs_chunks_dir  TEXT        NOT NULL,   -- e.g. users/uid/documents/did/chunks/
    status          TEXT        NOT NULL DEFAULT 'uploading',
    chunk_count     INTEGER     DEFAULT 0,
    error_message   TEXT,
    doc_metadata    JSONB       DEFAULT '{{}}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_user_id  ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_status   ON documents(status);

-- ── Document chunks (pgvector) ──────────────────────────────────────────────
-- Populated only when the user explicitly triggers embedding.
CREATE TABLE IF NOT EXISTS document_chunks (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id     UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chunk_index     INTEGER     NOT NULL,
    chunk_total     INTEGER     NOT NULL,
    content         TEXT        NOT NULL,
    embedding       vector({EMBEDDING_DIM}),
    chunk_metadata  JSONB       DEFAULT '{{}}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_user_id     ON document_chunks(user_id);

-- HNSW index for cosine similarity search (pgvector >= 0.5)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'document_chunks'
          AND indexname  = 'idx_chunks_embedding_hnsw'
    ) THEN
        EXECUTE 'CREATE INDEX idx_chunks_embedding_hnsw
                 ON document_chunks USING hnsw (embedding vector_cosine_ops)
                 WITH (m = 16, ef_construction = 64)';
    END IF;
EXCEPTION WHEN others THEN
    -- Older pgvector — index can be created manually later
    NULL;
END;
$$;
"""


async def create_tables() -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(CREATE_SCHEMA)
    print("✓ Database schema ready")
