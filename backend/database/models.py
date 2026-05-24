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


# ── Additional tables appended to CREATE_SCHEMA ───────────────────────────────
CREATE_SCHEMA_ADDITIONS = """

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS doc_type      TEXT,
    ADD COLUMN IF NOT EXISTS doc_domain    TEXT,
    ADD COLUMN IF NOT EXISTS doc_language  TEXT DEFAULT 'en',
    ADD COLUMN IF NOT EXISTS classified_at TIMESTAMPTZ;


-- Hybrid search: full-text search vector column
ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS search_vector tsvector;

-- GIN index for fast full-text search
CREATE INDEX IF NOT EXISTS idx_chunks_fts
    ON document_chunks USING gin(search_vector);



-- Message feedback (thumbs up/down on AI responses)
CREATE TABLE IF NOT EXISTS message_feedback (
    id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id UUID        REFERENCES chat_sessions(id) ON DELETE SET NULL,
    message_id TEXT        NOT NULL,
    rating     SMALLINT    NOT NULL CHECK (rating IN (-1, 1)),
    question   TEXT,
    answer     TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_feedback_user ON message_feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_feedback_session ON message_feedback(session_id);

-- Document tags (folders/collections)
CREATE TABLE IF NOT EXISTS document_tags (
    id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT        NOT NULL,
    color      TEXT        NOT NULL DEFAULT '#4ade80',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_dtags_user ON document_tags(user_id);

CREATE TABLE IF NOT EXISTS document_tag_map (
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag_id      UUID NOT NULL REFERENCES document_tags(id) ON DELETE CASCADE,
    PRIMARY KEY (document_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_dtmap_doc ON document_tag_map(document_id);
CREATE INDEX IF NOT EXISTS idx_dtmap_tag ON document_tag_map(tag_id);

-- Document classification
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS doc_type      TEXT,
    ADD COLUMN IF NOT EXISTS doc_domain    TEXT,
    ADD COLUMN IF NOT EXISTS doc_language  TEXT DEFAULT 'en',
    ADD COLUMN IF NOT EXISTS classified_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_docs_type   ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_docs_domain ON documents(doc_domain);

-- Stripe billing
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS stripe_customer_id      TEXT,
    ADD COLUMN IF NOT EXISTS stripe_subscription_id  TEXT,
    ADD COLUMN IF NOT EXISTS subscription_status     TEXT NOT NULL DEFAULT 'inactive',
    ADD COLUMN IF NOT EXISTS subscription_period_end TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_users_stripe_customer ON users(stripe_customer_id);

-- Email verification
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_verified              BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS verification_token_hash  TEXT,
    ADD COLUMN IF NOT EXISTS verification_token_exp   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS notify_on_embed          BOOLEAN     NOT NULL DEFAULT TRUE;

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id       UUID        REFERENCES users(id) ON DELETE SET NULL,
    action        TEXT        NOT NULL,
    resource_type TEXT,
    resource_id   TEXT,
    metadata      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    ip_address    TEXT,
    user_agent    TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_user    ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action  ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);

-- Workspaces (team collaboration)
CREATE TABLE IF NOT EXISTS workspaces (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT        NOT NULL,
    owner_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_workspaces_owner ON workspaces(owner_id);

-- Workspace members with roles
CREATE TABLE IF NOT EXISTS workspace_members (
    id           UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id      UUID        NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    role         TEXT        NOT NULL DEFAULT 'viewer'
                             CHECK (role IN ('owner','editor','viewer')),
    invited_by   UUID        REFERENCES users(id) ON DELETE SET NULL,
    joined_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (workspace_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_wm_workspace ON workspace_members(workspace_id);
CREATE INDEX IF NOT EXISTS idx_wm_user      ON workspace_members(user_id);

-- Attach documents + chunks to a workspace (NULL = personal)
ALTER TABLE documents      ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL;
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_docs_workspace   ON documents(workspace_id);
CREATE INDEX IF NOT EXISTS idx_chunks_workspace ON document_chunks(workspace_id);

-- User tier + custom limits (billing / tiered enforcement)
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS tier          TEXT    NOT NULL DEFAULT 'free',
    ADD COLUMN IF NOT EXISTS custom_limits JSONB;

-- Usage events (metering every billable action)
CREATE TABLE IF NOT EXISTS usage_events (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type  TEXT        NOT NULL,   -- upload | chunk | embedding | query | summarize | compare
    quantity    INTEGER     NOT NULL DEFAULT 1,
    metadata    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usage_user      ON usage_events(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_type      ON usage_events(event_type);
CREATE INDEX IF NOT EXISTS idx_usage_created   ON usage_events(created_at DESC);

-- Password reset tokens
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT        NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    used        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_prt_token_hash ON password_reset_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_prt_user_id    ON password_reset_tokens(user_id);

-- Chat sessions (persistent across devices)
CREATE TABLE IF NOT EXISTS chat_sessions (
    id           UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id      UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id UUID        REFERENCES workspaces(id) ON DELETE CASCADE,
    title        TEXT        NOT NULL DEFAULT 'New Chat',
    document_ids JSONB       NOT NULL DEFAULT '[]'::jsonb,
    messages     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_sessions_user_id      ON chat_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated_at   ON chat_sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_workspace_id ON chat_sessions(workspace_id);

"""


async def create_additional_tables() -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(CREATE_SCHEMA_ADDITIONS)
        # Backfill search_vector for existing chunks (inside same connection)
        await conn.execute("""
            UPDATE document_chunks
            SET search_vector = to_tsvector('english', content)
            WHERE search_vector IS NULL AND content IS NOT NULL AND content != ''
        """)
    print("✓ Additional schema ready (password_reset_tokens, chat_sessions, hybrid search FTS)")


async def create_eval_tables() -> None:
    """Create evaluation suite tables — called at startup."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS eval_suites (
                id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
                owner_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name        TEXT        NOT NULL,
                eval_type   TEXT        NOT NULL,
                description TEXT,
                created_at  TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS eval_cases (
                id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
                suite_id        UUID        NOT NULL REFERENCES eval_suites(id) ON DELETE CASCADE,
                document_id     UUID        REFERENCES documents(id) ON DELETE SET NULL,
                question        TEXT        NOT NULL,
                expected_answer TEXT,
                expected_fields JSONB       DEFAULT '{}'::jsonb,
                metadata        JSONB       DEFAULT '{}'::jsonb,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS eval_runs (
                id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
                suite_id      UUID        NOT NULL REFERENCES eval_suites(id) ON DELETE CASCADE,
                run_by        UUID        REFERENCES users(id) ON DELETE SET NULL,
                status        TEXT        NOT NULL DEFAULT 'pending',
                overall_score FLOAT,
                total_cases   INT         DEFAULT 0,
                passed_cases  INT         DEFAULT 0,
                metadata      JSONB       DEFAULT '{}'::jsonb,
                started_at    TIMESTAMPTZ DEFAULT NOW(),
                completed_at  TIMESTAMPTZ
            );
            CREATE TABLE IF NOT EXISTS eval_results (
                id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
                run_id          UUID        NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
                case_id         UUID        NOT NULL REFERENCES eval_cases(id) ON DELETE CASCADE,
                actual_answer   TEXT,
                actual_chunks   JSONB       DEFAULT '[]'::jsonb,
                score           FLOAT,
                passed          BOOLEAN,
                judge_verdict   TEXT,
                judge_reasoning TEXT,
                error_message   TEXT,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_eval_cases_suite  ON eval_cases(suite_id);
            CREATE INDEX IF NOT EXISTS idx_eval_results_run  ON eval_results(run_id);
            CREATE INDEX IF NOT EXISTS idx_eval_runs_suite   ON eval_runs(suite_id);
        """)
    print("\u2713 Eval tables ready")