-- Production-grade persisted agent workflow evaluation results.
-- Supports lease and generic vertical agent workflow runs.

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
