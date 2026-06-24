"""The Phase-0 pipeline: folder -> look -> arrange, in one call.

This is the whole assembly line wired together. ingest records the photos, vision
looks at each one, arrange lays them out into spreads. The show station (main.py)
reads the result.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from mnemosyne import arrange, config, ingest, storage, vision


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
        # Ingest has now copied each photo's bytes into storage under an `a<id>/`
        # key, so the web upload's staging folder is a redundant second copy. Drop
        # it to avoid silently doubling disk per album (R20). Containment-guarded,
        # so a CLI album's own gallery (outside UPLOAD_DIR) is never touched.
        _maybe_remove_upload_dir(row["source_dir"])

    looked = vision.look_at_album(conn, album_id)
    spreads = arrange.arrange_album(conn, album_id)
    return {"album_id": album_id, "looked": looked, "spreads": spreads}


def regenerate_layout(conn: sqlite3.Connection, album_id: int) -> int:
    """Re-run only the arrange step for a ready album.

    Vision scores and photo bytes are preserved; spreads and placements are
    replaced. Manual nudges (move spread/hero/slot) are lost — the owner opts
    in via the UI confirm dialog.
    """
    row = conn.execute(
        "SELECT status FROM albums WHERE id = ?", (album_id,)
    ).fetchone()
    if row is None or row["status"] != "ready":
        raise LookupError("album must be ready to regenerate layout")
    return arrange.arrange_album(conn, album_id)


def requeue_album(conn: sqlite3.Connection, album_id: int) -> bool:
    """Send a FAILED album back to 'pending' so the worker retries it (clearing
    the stale error). Returns True if it actually re-queued one — only failed
    albums qualify, so this can't disturb a ready or in-flight album."""
    cur = conn.execute(
        "UPDATE albums SET status = 'pending', error = NULL, "
        "claimed_at = NULL, claim_token = NULL, started_at = NULL, "
        "finished_at = NULL, last_heartbeat = NULL "
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
    before photos), then photos, then the album. The album's stored bytes are
    dropped too, via the storage seam's `a<id>/` key prefix; the source folder is
    additionally removed ONLY when it lives under UPLOAD_DIR, so deleting a CLI
    album never touches the operator's original gallery on disk.
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
    # Metering rows FK the album, so they go before it (no CASCADE in the schema).
    conn.execute("DELETE FROM inference_usage WHERE album_id = ?", (album_id,))
    conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    conn.commit()

    # Drop the album's stored bytes through the seam — `a<id>/` is this album's key
    # prefix, so this clears local-disk folders today and bucket objects tomorrow.
    # (No-op for legacy abspath-key albums, whose bytes live under source_dir.)
    storage.get_storage().delete_prefix(f"a{album_id}/")
    # Still clean the source folder: for a legacy album that IS where the bytes are,
    # and for a freshly-deleted web album whose staging may linger if ingest never ran.
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
