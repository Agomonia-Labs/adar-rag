-- Restaurant customer feedback MVP.
-- Supports customer ratings, verified-order feedback, restaurant owner response workflow,
-- and recommendation signals for menu search/compare.

CREATE TABLE IF NOT EXISTS restaurant_feedback (
    id                 UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    restaurant_id      UUID        NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    menu_item_id       UUID        REFERENCES restaurant_menu_items(id) ON DELETE SET NULL,
    order_id           UUID        REFERENCES restaurant_orders(id) ON DELETE SET NULL,
    customer_user_id   UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id       UUID        REFERENCES workspaces(id) ON DELETE SET NULL,
    rating             SMALLINT    NOT NULL CHECK (rating >= 1 AND rating <= 5),
    feedback_text      TEXT,
    language           TEXT        DEFAULT 'en',
    source_type        TEXT        NOT NULL DEFAULT 'manual',
    tags               JSONB       NOT NULL DEFAULT '[]'::jsonb,
    signals            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    verified_order     BOOLEAN     NOT NULL DEFAULT FALSE,
    status             TEXT        NOT NULL DEFAULT 'submitted',
    owner_response     TEXT,
    metadata           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_restaurant ON restaurant_feedback(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_menu       ON restaurant_feedback(menu_item_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_order      ON restaurant_feedback(order_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_workspace  ON restaurant_feedback(workspace_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_customer   ON restaurant_feedback(customer_user_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_status     ON restaurant_feedback(status);
CREATE INDEX IF NOT EXISTS idx_restaurant_feedback_created    ON restaurant_feedback(created_at DESC);
