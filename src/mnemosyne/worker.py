"""Background album worker — drains 'pending' albums through the vision pipeline.

The web upload route returns immediately with a 'pending' album so a big gallery
can't hang the request; this single daemon thread picks those up and runs the
slow vision/arrange work, flipping each album to 'ready' or 'failed'. State lives
on the album row (status + error + attempts + timestamps + claim_token), so
progress is observable from the DB alone — no separate queue to inspect.

Safe to run as more than one process. Claiming is atomic (a conditional UPDATE,
so two workers never grab the same album), and crash recovery is lease-based: a
'processing' album whose claim has gone stale is assumed abandoned by a dead
worker and re-queued, while a live sibling's fresh claim is left alone. Completion
is claim-token checked, so a reclaimed older worker cannot overwrite the newer
claim's result. There is no heartbeat, so a job that outruns the lease can be
re-run — harmless because process_album is idempotent (see
config.WORKER_LEASE_SECONDS).
"""
from __future__ import annotations

import logging
import secrets
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
        "UPDATE albums SET status = 'pending', claimed_at = NULL, claim_token = NULL, "
        "last_heartbeat = NULL "
        "WHERE status = 'processing' "
        "AND (claimed_at IS NULL OR claimed_at < datetime('now', ?))",
        (f"-{int(lease_seconds)} seconds",),
    )
    conn.commit()
    return cur.rowcount


def _claim_one(conn: sqlite3.Connection) -> tuple[int, str] | None:
    """Atomically take the oldest pending album and mark it 'processing', stamping
    the lease and a per-claim token. Safe across processes: the claim is a
    conditional UPDATE, and SQLite serializes writers, so only the first worker to
    flip a given row out of 'pending' wins (rowcount 1); a worker that loses the
    race sees rowcount 0 and tries the next candidate. Returns the claimed
    (album id, claim token), or None when nothing is pending."""
    while True:
        row = conn.execute(
            "SELECT id FROM albums WHERE status = 'pending' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        claim_token = secrets.token_urlsafe(24)
        cur = conn.execute(
            "UPDATE albums SET status = 'processing', claimed_at = datetime('now'), "
            "claim_token = ?, attempts = attempts + 1, started_at = datetime('now'), "
            "finished_at = NULL, last_heartbeat = datetime('now'), error = NULL "
            "WHERE id = ? AND status = 'pending'",
            (claim_token, row["id"]),
        )
        conn.commit()
        if cur.rowcount == 1:
            return row["id"], claim_token
        # Another worker claimed this one between our SELECT and UPDATE — try again.


def _finish_claim(
    conn: sqlite3.Connection,
    album_id: int,
    claim_token: str,
    status: str,
    error: str | None,
) -> bool:
    """Finish a job only if this worker still owns the live claim."""
    cur = conn.execute(
        "UPDATE albums SET status = ?, error = ?, claimed_at = NULL, "
        "claim_token = NULL, finished_at = datetime('now'), "
        "last_heartbeat = NULL WHERE id = ? AND status = 'processing' "
        "AND claim_token = ?",
        (status, error, album_id, claim_token),
    )
    conn.commit()
    return cur.rowcount == 1


def _run_one(conn: sqlite3.Connection, album_id: int, claim_token: str) -> None:
    """Process one claimed album, recording the outcome on its row. Any failure
    becomes status='failed' with the reason, never a crashed worker thread. The
    final write is guarded by the same claim token, so a worker whose lease was
    reclaimed cannot clobber the newer owner's result."""
    try:
        summary = pipeline.process_album(conn, album_id)
        if _finish_claim(conn, album_id, claim_token, "ready", None):
            log.info("album %s ready: %s", album_id, summary)
        else:
            log.info("album %s finished after its claim was superseded", album_id)
    except Exception as exc:  # noqa: BLE001 — a bad album must not kill the worker
        conn.rollback()
        if _finish_claim(conn, album_id, claim_token, "failed", str(exc)[:500]):
            log.exception("album %s failed", album_id)
        else:
            log.exception("album %s failed after its claim was superseded", album_id)


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
                claim = _claim_one(conn)
                if claim is None:
                    # Nothing to claim — sweep for any sibling that died holding a
                    # 'processing' album, then wait for a wake-up (or poll anyway).
                    if reclaim_stale(conn):
                        continue
                    self._wake.wait(timeout=_IDLE_POLL_SECONDS)
                    self._wake.clear()
                    continue
                album_id, claim_token = claim
                _run_one(conn, album_id, claim_token)
        finally:
            conn.close()
