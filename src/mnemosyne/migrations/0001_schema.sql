-- 0001_schema: the four tables Phase 0 needs (albums, photos, spreads,
-- placements). FORWARD-ONLY: once this has been applied anywhere, never edit it
-- — add 0002_*.sql instead. The migration runner applies files in name order.

-- An album is ONE run of mnemosyne over ONE folder of photos. source_dir
-- remembers where the images came from so a run is reproducible.
CREATE TABLE albums (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    source_dir  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per image found in the folder. width/height (in pixels) let us tell a
-- tall portrait from a wide landscape, which the layout step cares about. scene
-- and hero_score are NULL at ingest and filled later by the "look" (vision)
-- step — they're nullable precisely because a photo is recorded before any AI
-- has seen it.
CREATE TABLE photos (
    id          INTEGER PRIMARY KEY,
    album_id    INTEGER NOT NULL REFERENCES albums(id),
    path        TEXT NOT NULL,
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    scene       TEXT,                 -- vision tag, e.g. "wide ceremony shot"
    hero_score  REAL,                 -- 0..1, how album-cover-worthy the shot is
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Pages are read a whole album at a time, so index the album they belong to.
CREATE INDEX idx_photos_album ON photos(album_id);

-- A spread is one two-page unit of the album, ordered by position (1, 2, 3...).
-- hero_photo_id is the single photo on that spread that gets the large treatment;
-- nullable so a spread can be created before its hero is chosen.
CREATE TABLE spreads (
    id              INTEGER PRIMARY KEY,
    album_id        INTEGER NOT NULL REFERENCES albums(id),
    position        INTEGER NOT NULL,
    hero_photo_id   INTEGER REFERENCES photos(id)
);

CREATE INDEX idx_spreads_album ON spreads(album_id);

-- Which photos sit on which spread, and in what slot order (1..N within the
-- spread). This is the join between spreads and photos.
CREATE TABLE placements (
    id          INTEGER PRIMARY KEY,
    spread_id   INTEGER NOT NULL REFERENCES spreads(id),
    photo_id    INTEGER NOT NULL REFERENCES photos(id),
    slot        INTEGER NOT NULL
);

CREATE INDEX idx_placements_spread ON placements(spread_id);
