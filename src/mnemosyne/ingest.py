"""Ingest — the first station: a folder of images becomes rows in the database.

No AI here. We find the image files, read each one's pixel dimensions (which is
all we need to tell portrait from landscape), and record a photo row per image
under a freshly created album. The "look" step fills scene tags and hero scores
in later.
"""
from __future__ import annotations

import io
import sqlite3
from pathlib import Path

from PIL import Image, ImageOps

from mnemosyne import storage
from mnemosyne.themes import normalize_theme

# The file types we treat as album-eligible photos. Anything else in the folder
# (sidecar files, .DS_Store, raw .CR2/.NEF) is ignored for Phase 0.
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def create_album(
    conn: sqlite3.Connection,
    *,
    name: str,
    source_dir: str | Path,
    owner_id: int,
    status: str = "ready",
    gallery_theme: str = "food",
    mise_gallery_id: int | None = None,
    plutus_run_id: int | None = None,
) -> int:
    """Insert the album row and return its id, without touching photos. The one
    place albums come into being, so both the synchronous CLI path and the async
    web path agree on the columns. status defaults to 'ready' (the CLI builds
    fully before returning); the web path passes 'pending' so the worker can pick
    it up. owner_id is required — no album is ever created ownerless from here."""
    theme = normalize_theme(gallery_theme)
    cur = conn.execute(
        "INSERT INTO albums (name, source_dir, owner_id, status, gallery_theme, "
        "mise_gallery_id, plutus_run_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            name,
            str(Path(source_dir).expanduser()),
            owner_id,
            status,
            theme,
            mise_gallery_id,
            plutus_run_id,
        ),
    )
    conn.commit()
    return cur.lastrowid


def ingest_photos(
    conn: sqlite3.Connection, album_id: int, source_dir: str | Path
) -> int:
    """Record every image in source_dir as a photo under an EXISTING album, and
    return how many were added. Split out from album creation so the background
    worker can ingest into the pending album it's processing. Files are taken in
    sorted filename order — for camera/Lightroom exports that's usually
    chronological, giving the arrange step a sane starting sequence."""
    src = Path(source_dir).expanduser()
    if not src.is_dir():
        raise NotADirectoryError(f"not a folder: {src}")

    store = storage.get_storage()
    added = 0
    for path in sorted(src.iterdir()):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        # Read the full bytes and hand them to the storage driver — the bytes enter
        # storage THROUGH the seam, so they land on local disk today or in a bucket
        # tomorrow with no change here. The key is opaque + album-scoped, so two
        # albums can hold a same-named file and a whole album's bytes drop in one
        # delete_prefix later. Dimensions come from those same bytes (no second read).
        data = path.read_bytes()
        with Image.open(io.BytesIO(data)) as im:
            width, height = ImageOps.exif_transpose(im).size
        key = f"a{album_id}/{path.name}"
        store.put(key, data)
        conn.execute(
            "INSERT INTO photos (album_id, storage_key, width, height) "
            "VALUES (?, ?, ?, ?)",
            (album_id, key, width, height),
        )
        added += 1

    conn.commit()
    return added


def ingest_folder(
    conn: sqlite3.Connection,
    *,
    name: str,
    source_dir: str | Path,
    owner_id: int,
    gallery_theme: str = "food",
) -> int:
    """Create an album for source_dir, owned by owner_id, and record every image.

    Returns the new album's id. The synchronous path (CLI `build`): the album is
    'ready' the moment this returns because ingest happens inline here.
    """
    src = Path(source_dir).expanduser()
    if not src.is_dir():
        raise NotADirectoryError(f"not a folder: {src}")
    album_id = create_album(
        conn, name=name, source_dir=src, owner_id=owner_id, gallery_theme=gallery_theme
    )
    ingest_photos(conn, album_id, src)
    return album_id


def list_photos(conn: sqlite3.Connection, album_id: int) -> list[dict]:
    """Every photo in an album, in id order (i.e. ingest order)."""
    rows = conn.execute(
        "SELECT * FROM photos WHERE album_id = ? ORDER BY id", (album_id,)
    ).fetchall()
    return [dict(row) for row in rows]
