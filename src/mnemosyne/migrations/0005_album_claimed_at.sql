-- 0005: claimed_at — the lease stamp that makes the queue safe for more than one
-- worker process. A worker writes datetime('now') here at the instant it flips an
-- album to 'processing'. Crash recovery then uses the stamp's age to tell a dead
-- worker's abandoned job (stale stamp -> reclaim) from a live sibling's in-flight
-- job (fresh stamp -> leave alone), replacing the old "any processing row is
-- stuck" boot reset that was only correct with a single worker. NULL means a
-- claim from an older/unknown worker and is treated as stale.
ALTER TABLE albums ADD COLUMN claimed_at TEXT;
