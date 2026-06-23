-- 0003_users: accounts + multi-tenancy. The Phase-0/landing world was single-
-- user and local; this is the spine that lets strangers sign up and see ONLY
-- their own galleries. FORWARD-ONLY: never edit once applied, add 0004_*.sql.

-- One row per account. email is the login handle, stored normalized (trimmed +
-- lowercased by the code that inserts it) so UNIQUE catches casing variants the
-- same way the waitlist does. password_hash is a self-describing pbkdf2 string
-- ("pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>") — the salt and cost live
-- inside it, so verification needs nothing else and the cost can be raised later
-- without a schema change. We NEVER store the password itself.
CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Ownership lives on the album: photos/spreads/placements already hang off
-- album_id, so the whole tree inherits one owner through it. Nullable on purpose
-- — albums created before this migration (local dogfood) have no owner yet, and
-- the CLI `adduser` step adopts those orphans into the first real account. Every
-- album created from here on is stamped with its owner at ingest.
ALTER TABLE albums ADD COLUMN owner_id INTEGER REFERENCES users(id);

CREATE INDEX idx_albums_owner ON albums(owner_id);
