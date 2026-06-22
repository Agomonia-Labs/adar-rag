-- Healthcare RBAC persona assignments and field-level change history.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

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
