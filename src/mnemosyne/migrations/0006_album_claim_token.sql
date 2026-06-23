-- 0006: claim_token — a per-worker ownership token for the processing lease.
-- claimed_at decides when a lease is stale enough to reclaim; claim_token decides
-- whether a worker still owns the row when it tries to write the final result.
-- This prevents an older worker, whose lease was reclaimed, from overwriting the
-- newer worker's ready/failed outcome.
ALTER TABLE albums ADD COLUMN claim_token TEXT;
CREATE INDEX idx_albums_status_claimed ON albums(status, claimed_at);
