-- 0007: durable job metadata for the album worker state machine.
-- attempts counts real worker claims, started_at/finished_at bracket the latest
-- attempt, and last_heartbeat records the worker's most recent liveness stamp.
-- These fields make retries/reclaims/support checks observable without scraping
-- logs or inferring intent from status alone.
ALTER TABLE albums ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE albums ADD COLUMN started_at TEXT;
ALTER TABLE albums ADD COLUMN finished_at TEXT;
ALTER TABLE albums ADD COLUMN last_heartbeat TEXT;
