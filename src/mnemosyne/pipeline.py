"""The Phase-0 pipeline: folder -> look -> arrange, in one call.

This is the whole assembly line wired together. ingest records the photos, vision
looks at each one, arrange lays them out into spreads. The show station (main.py)
reads the result.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mnemosyne import arrange, ingest, vision


def build_album(
    conn: sqlite3.Connection, *, name: str, source_dir: str | Path, owner_id: int
) -> dict:
    """Run a folder all the way through to a laid-out album, synchronously. Used
    by the CLI `build`, where blocking the caller is fine. The web path uses
    enqueue_album + the worker instead. Returns a small summary (album id + how
    many photos were analyzed + how many spreads)."""
    album_id = ingest.ingest_folder(
        conn, name=name, source_dir=source_dir, owner_id=owner_id
    )
    looked = vision.look_at_album(conn, album_id)
    spreads = arrange.arrange_album(conn, album_id)
    return {"album_id": album_id, "looked": looked, "spreads": spreads}


def enqueue_album(
    conn: sqlite3.Connection, *, name: str, source_dir: str | Path, owner_id: int
) -> int:
    """Create a 'pending' album and return its id WITHOUT running the pipeline.
    The web upload route calls this so it can redirect immediately; the background
    worker does the slow vision work and flips the album to 'ready'. The photos
    already live in source_dir (the route saved them) — the worker ingests them
    when it processes the album, so nothing here reads images."""
    return ingest.create_album(
        conn, name=name, source_dir=source_dir, owner_id=owner_id, status="pending"
    )


def process_album(conn: sqlite3.Connection, album_id: int) -> dict:
    """Run the pipeline for an already-created album: ingest its source folder
    (once), look at every photo, lay out the spreads. This is the worker's body.

    Idempotent so a retry after a crash or failure is safe: photos are only
    ingested when the album has none yet, vision skips photos it already scored,
    and arrange rebuilds the layout from scratch. Raises if the album is missing
    or its source folder is gone — the worker turns that into a 'failed' status.
    """
    row = conn.execute(
        "SELECT source_dir FROM albums WHERE id = ?", (album_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"no such album: {album_id}")

    already = conn.execute(
        "SELECT COUNT(*) AS n FROM photos WHERE album_id = ?", (album_id,)
    ).fetchone()["n"]
    if already == 0:
        ingest.ingest_photos(conn, album_id, row["source_dir"])

    looked = vision.look_at_album(conn, album_id)
    spreads = arrange.arrange_album(conn, album_id)
    return {"album_id": album_id, "looked": looked, "spreads": spreads}


def requeue_album(conn: sqlite3.Connection, album_id: int) -> bool:
    """Send a FAILED album back to 'pending' so the worker retries it (clearing
    the stale error). Returns True if it actually re-queued one — only failed
    albums qualify, so this can't disturb a ready or in-flight album."""
    cur = conn.execute(
        "UPDATE albums SET status = 'pending', error = NULL "
        "WHERE id = ? AND status = 'failed'",
        (album_id,),
    )
    conn.commit()
    return cur.rowcount > 0
