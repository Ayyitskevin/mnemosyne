"""Background album worker — drains 'pending' albums through the vision pipeline.

The web upload route returns immediately with a 'pending' album so a big gallery
can't hang the request; this single daemon thread picks those up and runs the
slow vision/arrange work, flipping each album to 'ready' or 'failed'. State lives
on the album row (status + error), so progress is observable from the DB alone —
no separate queue to inspect.

Assumes a single server process (one worker thread). The CLI `serve` runs uvicorn
with one worker, so that holds; scaling to multiple processes would need a real
job queue with atomic claiming, which is the documented next turn.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from mnemosyne import db, pipeline

log = logging.getLogger("mnemosyne.worker")

# How long the idle worker waits for a wake-up before polling anyway. The route
# calls notify() on enqueue so jobs normally start at once; this is just the
# fallback so nothing sits forever if a notify is ever missed.
_IDLE_POLL_SECONDS = 2.0


def recover_stuck(conn: sqlite3.Connection) -> int:
    """Reset any album left 'processing' back to 'pending'. A 'processing' row at
    startup means the server died mid-job, so the work never finished — re-queue
    it (process_album is idempotent, so retrying is safe). Returns how many were
    recovered. Call once on boot before starting the worker."""
    cur = conn.execute(
        "UPDATE albums SET status = 'pending' WHERE status = 'processing'"
    )
    conn.commit()
    return cur.rowcount


def _claim_one(conn: sqlite3.Connection) -> int | None:
    """Take the oldest pending album and mark it 'processing'. Single worker, so a
    plain select-then-update is safe (no two threads racing for the same row)."""
    row = conn.execute(
        "SELECT id FROM albums WHERE status = 'pending' ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE albums SET status = 'processing' WHERE id = ?", (row["id"],)
    )
    conn.commit()
    return row["id"]


def _run_one(conn: sqlite3.Connection, album_id: int) -> None:
    """Process one claimed album, recording the outcome on its row. Any failure
    becomes status='failed' with the reason, never a crashed worker thread."""
    try:
        summary = pipeline.process_album(conn, album_id)
        conn.execute(
            "UPDATE albums SET status = 'ready', error = NULL WHERE id = ?",
            (album_id,),
        )
        conn.commit()
        log.info("album %s ready: %s", album_id, summary)
    except Exception as exc:  # noqa: BLE001 — a bad album must not kill the worker
        conn.rollback()
        conn.execute(
            "UPDATE albums SET status = 'failed', error = ? WHERE id = ?",
            (str(exc)[:500], album_id),
        )
        conn.commit()
        log.exception("album %s failed", album_id)


class AlbumWorker:
    """Owns the worker thread and its dedicated DB connection. start() recovers
    stuck albums then loops; notify() wakes it for a freshly enqueued album;
    stop() drains and joins on shutdown."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        conn = db.connect(self.db_path)
        try:
            recovered = recover_stuck(conn)
            if recovered:
                log.info("requeued %s album(s) left processing at shutdown", recovered)
        finally:
            conn.close()
        self._thread = threading.Thread(
            target=self._loop, name="album-worker", daemon=True
        )
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        # The connection is created here, inside the worker thread, because a
        # sqlite3 connection may only be used from the thread that made it.
        conn = db.connect(self.db_path)
        try:
            while not self._stop.is_set():
                album_id = _claim_one(conn)
                if album_id is None:
                    self._wake.wait(timeout=_IDLE_POLL_SECONDS)
                    self._wake.clear()
                    continue
                _run_one(conn, album_id)
        finally:
            conn.close()
