# database/models.py
from database.connection import get_pool, EMBEDDING_DIM


CREATE_SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS users (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           TEXT        UNIQUE NOT NULL,
    hashed_password TEXT        NOT NULL,
    full_name       TEXT        NOT NULL DEFAULT '',
    role            TEXT        NOT NULL DEFAULT 'user',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'role'
    ) THEN
        ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user';
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS documents (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename        TEXT        NOT NULL,
    original_name   TEXT        NOT NULL,
    file_type       TEXT        NOT NULL,
    file_size       BIGINT      NOT NULL,
    gcs_source_path TEXT        NOT NULL,
    gcs_chunks_dir  TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'uploading',
    chunk_count     INTEGER     DEFAULT 0,
    error_message   TEXT,
    doc_metadata    JSONB       DEFAULT '{{}}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_status  ON documents(status);

CREATE TABLE IF NOT EXISTS document_chunks (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id     UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chunk_index     INTEGER     NOT NULL,
    chunk_total     INTEGER     NOT NULL,
    content         TEXT        NOT NULL,
    embedding       vector({{EMBEDDING_DIM}}),
    chunk_metadata  JSONB       DEFAULT '{{}}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_user_id     ON document_chunks(user_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'document_chunks' AND indexname = 'idx_chunks_embedding_hnsw'
    ) THEN
        EXECUTE 'CREATE INDEX idx_chunks_embedding_hnsw ON document_chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)';
    END IF;
EXCEPTION WHEN others THEN NULL;
END;
$$;
"""


async def create_tables() -> None:
    schema = CREATE_SCHEMA.replace("{EMBEDDING_DIM}", str(EMBEDDING_DIM))
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(schema)
    print("✓ Database schema ready")