-- Stripe billing spine (per photographer account).
ALTER TABLE users ADD COLUMN stripe_customer_id TEXT;
ALTER TABLE users ADD COLUMN billing_status TEXT NOT NULL DEFAULT 'trialing';