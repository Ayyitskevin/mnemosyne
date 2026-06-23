"""The Phase-0 pipeline: folder -> look -> arrange, in one call.

This is the whole assembly line wired together. ingest records the photos, vision
looks at each one, arrange lays them out into spreads. The show station (main.py)
reads the result.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from mnemosyne import arrange, config, ingest, vision


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
        "UPDATE albums SET status = 'pending', error = NULL, "
        "claimed_at = NULL, claim_token = NULL "
        "WHERE id = ? AND status = 'failed'",
        (album_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_album(conn: sqlite3.Connection, album_id: int) -> bool:
    """Permanently remove an album and everything that hangs off it. Returns True
    if a row was actually deleted (False if the id was missing or still active).

    Pending/processing albums are deliberately not hard-deleted: a worker may
    already hold their id, and SQLite can reuse deleted rowids. Waiting until the
    album is ready or failed keeps a late worker from writing into a newer album.

    The schema has no ON DELETE CASCADE and foreign keys are enforced, so children
    are deleted in FK-safe order: placements first (they point at both spreads and
    photos), then spreads (their hero_photo_id points at photos, so they must go
    before photos), then photos, then the album. The uploaded source folder is
    removed too — but ONLY when it lives under UPLOAD_DIR, so deleting a CLI album
    never touches the operator's original gallery on disk.
    """
    row = conn.execute(
        "SELECT source_dir, status FROM albums WHERE id = ?", (album_id,)
    ).fetchone()
    if row is None or row["status"] in {"pending", "processing"}:
        return False

    conn.execute(
        "DELETE FROM placements WHERE spread_id IN "
        "(SELECT id FROM spreads WHERE album_id = ?)",
        (album_id,),
    )
    conn.execute("DELETE FROM spreads WHERE album_id = ?", (album_id,))
    conn.execute("DELETE FROM photos WHERE album_id = ?", (album_id,))
    conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    conn.commit()

    _maybe_remove_upload_dir(row["source_dir"])
    return True


def _maybe_remove_upload_dir(source_dir: str | Path) -> None:
    """Delete an album's on-disk folder, but only if it sits inside UPLOAD_DIR.
    Web uploads land in UPLOAD_DIR/u<owner>_<token>/ (ours to clean up); a CLI
    album's source_dir is the operator's own gallery and must be left alone. The
    resolved-path containment check also stops a doctored '../' source_dir from
    escaping the upload root."""
    root = config.UPLOAD_DIR.resolve()
    try:
        path = Path(source_dir).resolve()
    except OSError:
        return
    if path == root or not path.is_relative_to(root):
        return
    shutil.rmtree(path, ignore_errors=True)
