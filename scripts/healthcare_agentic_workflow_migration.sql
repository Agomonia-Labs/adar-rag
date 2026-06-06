-- Healthcare / generic vertical Agentic AI workflow migration.
-- Apply in production before enabling /api/healthcare.

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
