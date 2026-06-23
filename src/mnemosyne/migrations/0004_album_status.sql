-- 0004_album_status: a build state on each album, so web uploads can run their
-- vision pipeline in the background instead of inside the request. The create
-- route now returns immediately with a 'pending' album; a worker drains pending
-- albums and flips them to 'ready' (or 'failed', with the reason). FORWARD-ONLY:
-- never edit once applied, add 0005_*.sql instead.

-- status defaults to 'ready' so every PRE-EXISTING album (the dogfood data and
-- anything the synchronous CLI `build` makes) renders exactly as before — only
-- albums created through the async web path are ever 'pending'/'processing'.
-- error holds the failure message for a 'failed' album, shown on its status page
-- and cleared on retry; NULL whenever the album isn't failed.
ALTER TABLE albums ADD COLUMN status TEXT NOT NULL DEFAULT 'ready';
ALTER TABLE albums ADD COLUMN error  TEXT;
