-- 0009_inference_usage: a row per BILLED cloud-inference call, so the per-album
-- COGS (the number that sets pricing in Phase 2) is observable from the database,
-- not just reconstructable from a log file (R14: automation stays observable).
-- Only the cloud backends write here — local Ollama calls are free and unmetered.
-- FORWARD-ONLY: never edit once applied, add 0010_*.sql instead.

-- album_id is a real FK so usage is tenant-scoped and cleaned on album delete;
-- photo_id is a bare integer (vision is per-photo, arrange is per-album -> NULL)
-- kept un-enforced so recording never has to order itself against the photos
-- cascade. cost_usd is NULL when no price is configured: tokens are the ground
-- truth, dollars are derived only once xAI's rates are set in .env.
CREATE TABLE inference_usage (
    id                INTEGER PRIMARY KEY,
    album_id          INTEGER REFERENCES albums(id),
    photo_id          INTEGER,
    stage             TEXT NOT NULL,   -- 'vision' | 'arrange'
    backend           TEXT NOT NULL,   -- 'grok'
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    latency_s         REAL,
    cost_usd          REAL,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- COGS is read a whole album at a time, so index the album it belongs to.
CREATE INDEX idx_inference_usage_album ON inference_usage(album_id);
