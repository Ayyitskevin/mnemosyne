-- 0016_proposal_cache: a derived, idempotent cache of the emitted proposal.
--
-- The worker contract wants a STABLE proposal per (gallery, request): a retry must
-- return the same proposal, not recompute a new one. The layout itself is already
-- reproducible (the deterministic arrange engine), and this table makes the
-- idempotency explicit and observable — the proposal JSON keyed by a request
-- fingerprint (theme + arrange backend + each eligible photo's signals), so a repeat
-- request is a cache hit returning byte-identical bytes. It is a CACHE, never a
-- second store of authority: arrange invalidates it whenever it rewrites the layout,
-- and deleting the album drops it. FORWARD-ONLY: never edit once applied.

CREATE TABLE proposal_cache (
    album_id    INTEGER NOT NULL REFERENCES albums(id),
    request_key TEXT NOT NULL,            -- fingerprint of the inputs that define the proposal
    proposal    TEXT NOT NULL,            -- the canonical proposal JSON
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (album_id, request_key)
);
