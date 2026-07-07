-- Restaurant carryout order payment fields.
-- Enables Stripe Checkout / webhook payment status tracking.

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
