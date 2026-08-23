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

-- Guest try-before-signup sessions. Guest documents still use the normal
-- documents/chunks pipeline, but are scoped by a random browser-held token.
CREATE TABLE IF NOT EXISTS guest_sessions (
    id                    UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    token_hash            TEXT        UNIQUE NOT NULL,
    guest_user_id          UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    claimed_by_user_id     UUID        REFERENCES users(id) ON DELETE SET NULL,
    claimed_at             TIMESTAMPTZ,
    expires_at             TIMESTAMPTZ NOT NULL,
    upload_count           INTEGER     NOT NULL DEFAULT 0,
    query_count            INTEGER     NOT NULL DEFAULT 0,
    created_at             TIMESTAMPTZ DEFAULT NOW(),
    updated_at             TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_guest_sessions_token_hash ON guest_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_guest_sessions_expires_at ON guest_sessions(expires_at);

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS guest_session_id UUID REFERENCES guest_sessions(id) ON DELETE SET NULL;

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS guest_session_id UUID REFERENCES guest_sessions(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_docs_guest_session ON documents(guest_session_id);
CREATE INDEX IF NOT EXISTS idx_chunks_guest_session ON document_chunks(guest_session_id);


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

-- Domain-specific personas layered on top of coarse workspace roles.
-- A user may be an editor in a workspace, but their healthcare scope can be
-- provider, nurse, billing, compliance, patient, caregiver, etc.
CREATE TABLE IF NOT EXISTS workspace_member_personas (
    id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id  UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id       UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    vertical      TEXT        NOT NULL,
    persona       TEXT        NOT NULL,
    assigned_by   UUID        REFERENCES users(id) ON DELETE SET NULL,
    assigned_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (workspace_id, user_id, vertical, persona)
);
CREATE INDEX IF NOT EXISTS idx_wmp_workspace ON workspace_member_personas(workspace_id);
CREATE INDEX IF NOT EXISTS idx_wmp_user      ON workspace_member_personas(user_id);
CREATE INDEX IF NOT EXISTS idx_wmp_vertical  ON workspace_member_personas(vertical);

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

-- Traceability: request flows, internal spans, and LLM/tool calls
CREATE TABLE IF NOT EXISTS trace_flows (
    id                 UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    trace_id           TEXT        UNIQUE NOT NULL,
    request_type       TEXT        NOT NULL,
    user_id            UUID        REFERENCES users(id) ON DELETE SET NULL,
    workspace_id       UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    session_id         UUID        REFERENCES chat_sessions(id) ON DELETE SET NULL,
    status             TEXT        NOT NULL DEFAULT 'running',
    input_text_hash    TEXT,
    input_text_preview TEXT,
    client_info        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    metadata           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error_message      TEXT,
    started_at         TIMESTAMPTZ DEFAULT NOW(),
    ended_at           TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_trace_flows_user    ON trace_flows(user_id);
CREATE INDEX IF NOT EXISTS idx_trace_flows_type    ON trace_flows(request_type);
CREATE INDEX IF NOT EXISTS idx_trace_flows_started ON trace_flows(started_at DESC);

CREATE TABLE IF NOT EXISTS trace_spans (
    id             UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    span_id        TEXT        UNIQUE NOT NULL,
    trace_id       TEXT        NOT NULL REFERENCES trace_flows(trace_id) ON DELETE CASCADE,
    parent_span_id TEXT,
    name           TEXT        NOT NULL,
    status         TEXT        NOT NULL DEFAULT 'running',
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    started_at     TIMESTAMPTZ DEFAULT NOW(),
    ended_at       TIMESTAMPTZ,
    duration_ms    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_trace_spans_trace ON trace_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_trace_spans_name  ON trace_spans(name);

CREATE TABLE IF NOT EXISTS trace_llm_events (
    id                UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_id          TEXT        UNIQUE NOT NULL,
    trace_id          TEXT        NOT NULL REFERENCES trace_flows(trace_id) ON DELETE CASCADE,
    span_id           TEXT        REFERENCES trace_spans(span_id) ON DELETE SET NULL,
    provider          TEXT        NOT NULL,
    model             TEXT,
    operation         TEXT        NOT NULL,
    system_prompt     TEXT,
    user_prompt       TEXT,
    tool_request_json JSONB       NOT NULL DEFAULT '{}'::jsonb,
    tool_response_json JSONB      NOT NULL DEFAULT '{}'::jsonb,
    llm_response      TEXT,
    input_tokens      INTEGER,
    output_tokens     INTEGER,
    latency_ms        INTEGER,
    finish_reason     TEXT,
    redaction_status  TEXT        NOT NULL DEFAULT 'redacted',
    error             TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trace_llm_trace ON trace_llm_events(trace_id);
CREATE INDEX IF NOT EXISTS idx_trace_llm_span  ON trace_llm_events(span_id);
CREATE INDEX IF NOT EXISTS idx_trace_llm_op    ON trace_llm_events(operation);

-- Real Estate / Lease Intelligence vertical layer
CREATE TABLE IF NOT EXISTS lease_abstracts (
    id             UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id    UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id        UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id   UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    abstract_data  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    confidence     FLOAT,
    status         TEXT        NOT NULL DEFAULT 'ready',
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(document_id)
);
CREATE INDEX IF NOT EXISTS idx_lease_abstracts_doc  ON lease_abstracts(document_id);
CREATE INDEX IF NOT EXISTS idx_lease_abstracts_user ON lease_abstracts(user_id);

CREATE TABLE IF NOT EXISTS lease_critical_dates (
    id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id   UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id       UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id  UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    date_type     TEXT        NOT NULL,
    date_value    DATE,
    raw_value     TEXT,
    description   TEXT,
    responsible_party TEXT,
    source        TEXT,
    confidence    FLOAT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lease_dates_doc  ON lease_critical_dates(document_id);
CREATE INDEX IF NOT EXISTS idx_lease_dates_user ON lease_critical_dates(user_id);
CREATE INDEX IF NOT EXISTS idx_lease_dates_date ON lease_critical_dates(date_value);

CREATE TABLE IF NOT EXISTS lease_clause_flags (
    id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id   UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id       UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id  UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    clause_type   TEXT        NOT NULL,
    status        TEXT        NOT NULL,
    risk_level    TEXT        NOT NULL DEFAULT 'unknown',
    finding       TEXT,
    source        TEXT,
    confidence    FLOAT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lease_flags_doc  ON lease_clause_flags(document_id);
CREATE INDEX IF NOT EXISTS idx_lease_flags_user ON lease_clause_flags(user_id);

CREATE TABLE IF NOT EXISTS lease_comparisons (
    id             UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    base_document_id UUID      NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    amendment_document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id        UUID       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id   UUID       REFERENCES workspaces(id) ON DELETE SET NULL,
    comparison_data JSONB     NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lease_comparisons_base ON lease_comparisons(base_document_id);
CREATE INDEX IF NOT EXISTS idx_lease_comparisons_user ON lease_comparisons(user_id);

CREATE TABLE IF NOT EXISTS lease_agent_runs (
    id                    UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id           UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    amendment_document_id UUID        REFERENCES documents(id) ON DELETE SET NULL,
    user_id               UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id          UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    workflow_version      TEXT        NOT NULL DEFAULT 'phase2-adk-v1',
    status                TEXT        NOT NULL DEFAULT 'running',
    result_data           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error_message         TEXT,
    approved_by           UUID        REFERENCES users(id) ON DELETE SET NULL,
    approved_at           TIMESTAMPTZ,
    approval_notes        TEXT,
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW(),
    completed_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lease_agent_runs_doc    ON lease_agent_runs(document_id);
CREATE INDEX IF NOT EXISTS idx_lease_agent_runs_user   ON lease_agent_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_lease_agent_runs_status ON lease_agent_runs(status);

CREATE TABLE IF NOT EXISTS lease_agent_steps (
    id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id        UUID        NOT NULL REFERENCES lease_agent_runs(id) ON DELETE CASCADE,
    agent_name    TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'pending',
    input_summary TEXT,
    output_data   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    started_at    TIMESTAMPTZ DEFAULT NOW(),
    completed_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lease_agent_steps_run   ON lease_agent_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_lease_agent_steps_agent ON lease_agent_steps(agent_name);

CREATE TABLE IF NOT EXISTS lease_obligations (
    id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id        UUID        NOT NULL REFERENCES lease_agent_runs(id) ON DELETE CASCADE,
    document_id   UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id       UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id  UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    title         TEXT        NOT NULL,
    party         TEXT        NOT NULL DEFAULT 'unknown',
    category      TEXT        NOT NULL DEFAULT 'other',
    priority      TEXT        NOT NULL DEFAULT 'medium',
    due_date      DATE,
    trigger       TEXT,
    source        TEXT,
    status        TEXT        NOT NULL DEFAULT 'open',
    notes         TEXT,
    approved      BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lease_obligations_doc      ON lease_obligations(document_id);
CREATE INDEX IF NOT EXISTS idx_lease_obligations_run      ON lease_obligations(run_id);
CREATE INDEX IF NOT EXISTS idx_lease_obligations_user     ON lease_obligations(user_id);
CREATE INDEX IF NOT EXISTS idx_lease_obligations_due_date ON lease_obligations(due_date);
CREATE INDEX IF NOT EXISTS idx_lease_obligations_status   ON lease_obligations(status);

-- Generic vertical Agentic AI workflow run store.
-- Lease currently has dedicated tables for its richer persistence; healthcare and
-- future verticals use this config-driven generic store.
CREATE TABLE IF NOT EXISTS vertical_agent_runs (
    id               UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id      TEXT        NOT NULL,
    workflow_version TEXT        NOT NULL DEFAULT 'v1',
    vertical         TEXT        NOT NULL,
    document_id      UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id          UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id     UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    status           TEXT        NOT NULL DEFAULT 'running',
    input_data       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    result_data      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error_message    TEXT,
    approved_by      UUID        REFERENCES users(id) ON DELETE SET NULL,
    approved_at      TIMESTAMPTZ,
    approval_notes   TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    completed_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_vertical_agent_runs_doc      ON vertical_agent_runs(document_id);
CREATE INDEX IF NOT EXISTS idx_vertical_agent_runs_user     ON vertical_agent_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_vertical_agent_runs_vertical ON vertical_agent_runs(vertical);
CREATE INDEX IF NOT EXISTS idx_vertical_agent_runs_status   ON vertical_agent_runs(status);

CREATE TABLE IF NOT EXISTS vertical_agent_steps (
    id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id        UUID        NOT NULL REFERENCES vertical_agent_runs(id) ON DELETE CASCADE,
    agent_name    TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'pending',
    input_summary TEXT,
    output_data   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    started_at    TIMESTAMPTZ DEFAULT NOW(),
    completed_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_vertical_agent_steps_run   ON vertical_agent_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_vertical_agent_steps_agent ON vertical_agent_steps(agent_name);

CREATE TABLE IF NOT EXISTS vertical_agent_field_changes (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id          UUID        NOT NULL REFERENCES vertical_agent_runs(id) ON DELETE CASCADE,
    document_id     UUID        REFERENCES documents(id) ON DELETE CASCADE,
    workspace_id    UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    vertical        TEXT        NOT NULL,
    workflow_id     TEXT        NOT NULL,
    user_id         UUID        REFERENCES users(id) ON DELETE SET NULL,
    workspace_role  TEXT,
    persona         TEXT        NOT NULL DEFAULT 'unknown',
    action_type     TEXT        NOT NULL,
    field_path      TEXT        NOT NULL,
    old_value       JSONB,
    new_value       JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vafc_run      ON vertical_agent_field_changes(run_id);
CREATE INDEX IF NOT EXISTS idx_vafc_doc      ON vertical_agent_field_changes(document_id);
CREATE INDEX IF NOT EXISTS idx_vafc_user     ON vertical_agent_field_changes(user_id);
CREATE INDEX IF NOT EXISTS idx_vafc_created  ON vertical_agent_field_changes(created_at DESC);

CREATE TABLE IF NOT EXISTS restaurants (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id    UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    source_run_id   UUID        REFERENCES vertical_agent_runs(id) ON DELETE SET NULL,
    name            TEXT        NOT NULL,
    description     TEXT        NOT NULL DEFAULT '',
    cuisine_type    TEXT        NOT NULL DEFAULT '',
    address         TEXT        NOT NULL DEFAULT '',
    phone           TEXT        NOT NULL DEFAULT '',
    email           TEXT        NOT NULL DEFAULT '',
    website         TEXT        NOT NULL DEFAULT '',
    hours           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    service_options JSONB       NOT NULL DEFAULT '[]'::jsonb,
    payment_options JSONB       NOT NULL DEFAULT '[]'::jsonb,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_restaurants_user      ON restaurants(user_id);
CREATE INDEX IF NOT EXISTS idx_restaurants_workspace ON restaurants(workspace_id);
CREATE INDEX IF NOT EXISTS idx_restaurants_name      ON restaurants(LOWER(name));

CREATE TABLE IF NOT EXISTS restaurant_menu_items (
    id             UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    restaurant_id  UUID        NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    user_id        UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id   UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    category       TEXT        NOT NULL DEFAULT '',
    item_name      TEXT        NOT NULL,
    price          NUMERIC(10,2),
    currency       TEXT        NOT NULL DEFAULT 'USD',
    quantity       TEXT        NOT NULL DEFAULT '',
    description    TEXT        NOT NULL DEFAULT '',
    ingredients    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    dietary_tags   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    spice_level    TEXT        NOT NULL DEFAULT '',
    availability   TEXT        NOT NULL DEFAULT 'available',
    options        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_menu_restaurant ON restaurant_menu_items(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_menu_user       ON restaurant_menu_items(user_id);
CREATE INDEX IF NOT EXISTS idx_menu_workspace  ON restaurant_menu_items(workspace_id);
CREATE INDEX IF NOT EXISTS idx_menu_name       ON restaurant_menu_items(LOWER(item_name));

CREATE TABLE IF NOT EXISTS restaurant_orders (
    id                   UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    restaurant_id        UUID        NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    customer_user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id         UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    status               TEXT        NOT NULL DEFAULT 'draft',
    fulfillment_type     TEXT        NOT NULL DEFAULT 'carryout',
    customer_name        TEXT        NOT NULL DEFAULT '',
    customer_phone       TEXT        NOT NULL DEFAULT '',
    customer_email       TEXT        NOT NULL DEFAULT '',
    pickup_time_request  TEXT        NOT NULL DEFAULT '',
    special_instructions TEXT        NOT NULL DEFAULT '',
    subtotal             NUMERIC(10,2) NOT NULL DEFAULT 0,
    currency             TEXT        NOT NULL DEFAULT 'USD',
    metadata             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    submitted_at         TIMESTAMPTZ,
    accepted_at          TIMESTAMPTZ,
    confirmed_at         TIMESTAMPTZ,
    ready_at             TIMESTAMPTZ,
    completed_at         TIMESTAMPTZ,
    cancelled_at         TIMESTAMPTZ,
    rejected_at          TIMESTAMPTZ,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_restaurant_orders_restaurant ON restaurant_orders(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_orders_customer   ON restaurant_orders(customer_user_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_orders_workspace  ON restaurant_orders(workspace_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_orders_status     ON restaurant_orders(status);
CREATE INDEX IF NOT EXISTS idx_restaurant_orders_created    ON restaurant_orders(created_at DESC);

ALTER TABLE restaurant_orders
    ADD COLUMN IF NOT EXISTS payment_status TEXT NOT NULL DEFAULT 'unpaid',
    ADD COLUMN IF NOT EXISTS payment_provider TEXT,
    ADD COLUMN IF NOT EXISTS stripe_checkout_session_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_payment_intent_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_refund_id TEXT,
    ADD COLUMN IF NOT EXISTS payment_amount NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS platform_fee_amount NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS refund_error TEXT;
CREATE INDEX IF NOT EXISTS idx_restaurant_orders_payment_status ON restaurant_orders(payment_status);
CREATE INDEX IF NOT EXISTS idx_restaurant_orders_stripe_session ON restaurant_orders(stripe_checkout_session_id);

CREATE TABLE IF NOT EXISTS restaurant_order_items (
    id             UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id       UUID        NOT NULL REFERENCES restaurant_orders(id) ON DELETE CASCADE,
    menu_item_id   UUID        REFERENCES restaurant_menu_items(id) ON DELETE SET NULL,
    item_name      TEXT        NOT NULL,
    category       TEXT        NOT NULL DEFAULT '',
    quantity       INTEGER     NOT NULL DEFAULT 1,
    unit_price     NUMERIC(10,2),
    line_total     NUMERIC(10,2) NOT NULL DEFAULT 0,
    currency       TEXT        NOT NULL DEFAULT 'USD',
    instructions   TEXT        NOT NULL DEFAULT '',
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_restaurant_order_items_order ON restaurant_order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_order_items_menu  ON restaurant_order_items(menu_item_id);

CREATE TABLE IF NOT EXISTS restaurant_order_events (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id    UUID        NOT NULL REFERENCES restaurant_orders(id) ON DELETE CASCADE,
    actor_id    UUID        REFERENCES users(id) ON DELETE SET NULL,
    event_type  TEXT        NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    notes       TEXT        NOT NULL DEFAULT '',
    metadata    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_restaurant_order_events_order   ON restaurant_order_events(order_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_order_events_created ON restaurant_order_events(created_at DESC);

CREATE TABLE IF NOT EXISTS restaurant_notifications (
    id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    restaurant_id UUID        NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    order_id      UUID        NOT NULL REFERENCES restaurant_orders(id) ON DELETE CASCADE,
    user_id       UUID        REFERENCES users(id) ON DELETE CASCADE,
    channel       TEXT        NOT NULL DEFAULT 'in_app',
    status        TEXT        NOT NULL DEFAULT 'unread',
    message       TEXT        NOT NULL DEFAULT '',
    metadata      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    read_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_restaurant_notifications_order ON restaurant_notifications(order_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_notifications_user  ON restaurant_notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_notifications_status ON restaurant_notifications(status);

CREATE TABLE IF NOT EXISTS restaurant_feedback (
    id             UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    restaurant_id  UUID        NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    menu_item_id   UUID        REFERENCES restaurant_menu_items(id) ON DELETE SET NULL,
    order_id       UUID        REFERENCES restaurant_orders(id) ON DELETE SET NULL,
    customer_user_id UUID      REFERENCES users(id) ON DELETE SET NULL,
    workspace_id   UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    rating         SMALLINT    NOT NULL CHECK (rating BETWEEN 1 AND 5),
    feedback_text  TEXT        NOT NULL DEFAULT '',
    language       TEXT        NOT NULL DEFAULT '',
    source_type    TEXT        NOT NULL DEFAULT 'text',
    tags           JSONB       NOT NULL DEFAULT '[]'::jsonb,
    signals        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    verified_order BOOLEAN     NOT NULL DEFAULT FALSE,
    status         TEXT        NOT NULL DEFAULT 'submitted',
    owner_response TEXT        NOT NULL DEFAULT '',
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW(),
    responded_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_restaurant ON restaurant_feedback(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_menu       ON restaurant_feedback(menu_item_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_order      ON restaurant_feedback(order_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_workspace  ON restaurant_feedback(workspace_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_customer   ON restaurant_feedback(customer_user_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_status     ON restaurant_feedback(status);
CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_created    ON restaurant_feedback(created_at DESC);

ALTER TABLE restaurant_feedback
    ADD COLUMN IF NOT EXISTS responded_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS talent_runs (
    id                    UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id               UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id          UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    resume_document_ids   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    job_description_id    UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    candidate_name        TEXT        NOT NULL DEFAULT '',
    workflow_version      TEXT        NOT NULL DEFAULT 'talent-mvp1-v1',
    status                TEXT        NOT NULL DEFAULT 'needs_review',
    packet                JSONB       NOT NULL DEFAULT '{}'::jsonb,
    reviewer_notes        TEXT        NOT NULL DEFAULT '',
    reviewed_by           UUID        REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at           TIMESTAMPTZ,
    approved_by           UUID        REFERENCES users(id) ON DELETE SET NULL,
    approved_at           TIMESTAMPTZ,
    error_message         TEXT,
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW(),
    completed_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_talent_runs_user ON talent_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_talent_runs_workspace ON talent_runs(workspace_id);
CREATE INDEX IF NOT EXISTS idx_talent_runs_job ON talent_runs(job_description_id);
CREATE INDEX IF NOT EXISTS idx_talent_runs_status ON talent_runs(status);

CREATE TABLE IF NOT EXISTS mcp_playground_sessions (
    session_hash             TEXT        PRIMARY KEY,
    user_id                  UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    oauth_state              TEXT        UNIQUE NOT NULL,
    client_id                TEXT        NOT NULL,
    code_verifier_encrypted  TEXT        NOT NULL,
    access_token_encrypted   TEXT,
    refresh_token_encrypted  TEXT,
    scopes                    TEXT        NOT NULL DEFAULT '',
    expires_at                TIMESTAMPTZ,
    connected_at             TIMESTAMPTZ,
    revoked_at               TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mcp_playground_user ON mcp_playground_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_mcp_playground_state ON mcp_playground_sessions(oauth_state);

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

            CREATE TABLE IF NOT EXISTS agent_workflow_evaluations (
                id                UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
                vertical          TEXT        NOT NULL,
                run_id            UUID        NOT NULL,
                document_id       UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                user_id           UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                workspace_id      UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
                evaluator_version TEXT        NOT NULL,
                overall_score     FLOAT       NOT NULL,
                passed            BOOLEAN     NOT NULL DEFAULT FALSE,
                gate_status       TEXT        NOT NULL DEFAULT 'needs_review',
                metrics           JSONB       NOT NULL DEFAULT '[]'::jsonb,
                recommendations   JSONB       NOT NULL DEFAULT '[]'::jsonb,
                policy            JSONB       NOT NULL DEFAULT '{}'::jsonb,
                metadata          JSONB       NOT NULL DEFAULT '{}'::jsonb,
                created_at        TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_agent_eval_vertical_run ON agent_workflow_evaluations(vertical, run_id);
            CREATE INDEX IF NOT EXISTS idx_agent_eval_document     ON agent_workflow_evaluations(document_id);
            CREATE INDEX IF NOT EXISTS idx_agent_eval_user         ON agent_workflow_evaluations(user_id);
            CREATE INDEX IF NOT EXISTS idx_agent_eval_workspace    ON agent_workflow_evaluations(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_agent_eval_gate         ON agent_workflow_evaluations(gate_status);
        """)
    print("\u2713 Eval tables ready")
