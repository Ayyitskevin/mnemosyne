-- 0010: share link — let an owner hand a finished album to someone with no
-- account. share_token is an unguessable capability (the link IS the auth, so the
-- private album_id never appears in a public URL); share_expires_at bounds the
-- exposure so a forwarded/leaked link stops working on its own. Both NULL = not
-- shared. Revoke clears them. The UNIQUE index makes token lookup an indexed point
-- read and guards against the (astronomically unlikely) duplicate token.
ALTER TABLE albums ADD COLUMN share_token TEXT;
ALTER TABLE albums ADD COLUMN share_expires_at TEXT;
CREATE UNIQUE INDEX idx_albums_share_token ON albums(share_token)
  WHERE share_token IS NOT NULL;
