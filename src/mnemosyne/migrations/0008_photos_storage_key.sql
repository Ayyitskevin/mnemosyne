-- 0008_photos_storage_key: the photos.path column now holds an OPAQUE storage key
-- (resolved only by storage.py), not a raw disk path — so the bytes can live on
-- local disk today and in an object store (R2/S3) tomorrow without a column or
-- caller change. Renaming it makes the model honest: callers must treat the value
-- as a key, never Path() it directly. FORWARD-ONLY: never edit once applied, add
-- 0009_*.sql instead.

-- Existing rows keep their value unchanged — pre-turn albums hold an absolute disk
-- path, which the local driver honors as a legacy passthrough so they stay
-- viewable. New albums get relative `a<album_id>/<file>` keys. Needs SQLite >= 3.25
-- for RENAME COLUMN (shipped 2018; the runtime here is well past it).
ALTER TABLE photos RENAME COLUMN path TO storage_key;
