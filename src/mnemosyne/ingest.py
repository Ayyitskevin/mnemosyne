"""Ingest — the first station: a folder of images becomes rows in the database.

No AI here. We find the image files, read each one's pixel dimensions (which is
all we need to tell portrait from landscape), and record a photo row per image
under a freshly created album. The "look" step fills scene tags and hero scores
in later.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from PIL import Image

# The file types we treat as album-eligible photos. Anything else in the folder
# (sidecar files, .DS_Store, raw .CR2/.NEF) is ignored for Phase 0.
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def ingest_folder(
    conn: sqlite3.Connection, *, name: str, source_dir: str | Path, owner_id: int
) -> int:
    """Create an album for source_dir, owned by owner_id, and record every image.

    Returns the new album's id. owner_id is required so no album is ever created
    ownerless from here on (orphans only exist for pre-accounts dogfood data).
    Files are taken in sorted filename order — for camera/Lightroom exports that's
    usually chronological, giving the arrange step a sane starting sequence.
    """
    src = Path(source_dir).expanduser()
    if not src.is_dir():
        raise NotADirectoryError(f"not a folder: {src}")

    cur = conn.execute(
        "INSERT INTO albums (name, source_dir, owner_id) VALUES (?, ?, ?)",
        (name, str(src), owner_id),
    )
    album_id = cur.lastrowid

    for path in sorted(src.iterdir()):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        # Pillow reads dimensions from the header without decoding the whole
        # image, so this stays fast even on a big gallery.
        with Image.open(path) as im:
            width, height = im.size
        conn.execute(
            "INSERT INTO photos (album_id, path, width, height) VALUES (?, ?, ?, ?)",
            (album_id, str(path), width, height),
        )

    conn.commit()
    return album_id


def list_photos(conn: sqlite3.Connection, album_id: int) -> list[dict]:
    """Every photo in an album, in id order (i.e. ingest order)."""
    rows = conn.execute(
        "SELECT * FROM photos WHERE album_id = ? ORDER BY id", (album_id,)
    ).fetchall()
    return [dict(row) for row in rows]
