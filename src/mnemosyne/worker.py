"""Background album worker — drains 'pending' albums through the vision pipeline.

The web upload route returns immediately with a 'pending' album so a big gallery
can't hang the request; this single daemon thread picks those up and runs the
slow vision/arrange work, flipping each album to 'ready' or 'failed'. State lives
on the album row (status + error + claimed_at), so progress is observable from
the DB alone — no separate queue to inspect.

Safe to run as more than one process. Claiming is atomic (a conditional UPDATE,
so two workers never grab the same album), and crash recovery is lease-based: a
'processing' album whose claim has gone stale is assumed abandoned by a dead
worker and re-queued, while a live sibling's fresh claim is left alone. There is
no heartbeat, so a job that outruns the lease can be re-run — harmless because
process_album is idempotent (see config.WORKER_LEASE_SECONDS).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from mnemosyne import config, db, pipeline

log = logging.getLogger("mnemosyne.worker")

# How long the idle worker waits for a wake-up before polling anyway. The route
# calls notify() on enqueue so jobs normally start at once; this is just the
# fallback so nothing sits forever if a notify is ever missed.
_IDLE_POLL_SECONDS = 2.0


def reclaim_stale(conn: sqlite3.Connection, lease_seconds: int | None = None) -> int:
    """Re-queue albums whose 'processing' lease has expired — the worker that
    claimed them died mid-job. claimed_at is the lease stamp; a row older than the
    lease (or with no stamp, from a pre-lease/unknown claim) is treated as
    abandoned and reset to 'pending' for another worker. A live sibling's job has
    a fresh stamp and is untouched, which is what makes this safe to call from
    every process — unlike a blanket reset. Returns how many were reclaimed.

    process_album is idempotent, so re-running a reclaimed album is safe even in
    the rare case a still-live job's lease expired before it finished."""
    if lease_seconds is None:
        lease_seconds = config.WORKER_LEASE_SECONDS
    cur = conn.execute(
        "UPDATE albums SET status = 'pending', claimed_at = NULL "
        "WHERE status = 'processing' "
        "AND (claimed_at IS NULL OR claimed_at < datetime('now', ?))",
        (f"-{int(lease_seconds)} seconds",),
    )
    conn.commit()
    return cur.rowcount


def _claim_one(conn: sqlite3.Connection) -> int | None:
    """Atomically take the oldest pending album and mark it 'processing', stamping
    the lease. Safe across processes: the claim is a conditional UPDATE, and
    SQLite serializes writers, so only the first worker to flip a given row out of
    'pending' wins (rowcount 1); a worker that loses the race sees rowcount 0 and
    tries the next candidate. Returns the claimed id, or None when nothing is
    pending."""
    while True:
        row = conn.execute(
            "SELECT id FROM albums WHERE status = 'pending' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute(
            "UPDATE albums SET status = 'processing', claimed_at = datetime('now') "
            "WHERE id = ? AND status = 'pending'",
            (row["id"],),
        )
        conn.commit()
        if cur.rowcount == 1:
            return row["id"]
        # Another worker claimed this one between our SELECT and UPDATE — try again.


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
            recovered = reclaim_stale(conn)
            if recovered:
                log.info("reclaimed %s album(s) from a stale/dead worker", recovered)
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
                    # Nothing to claim — sweep for any sibling that died holding a
                    # 'processing' album, then wait for a wake-up (or poll anyway).
                    if reclaim_stale(conn):
                        continue
                    self._wake.wait(timeout=_IDLE_POLL_SECONDS)
                    self._wake.clear()
                    continue
                _run_one(conn, album_id)
        finally:
            conn.close()
