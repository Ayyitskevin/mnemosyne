"""Database access for mnemosyne.

One SQLite file, opened in WAL mode, with a tiny forward-only migration runner.
Every other part of the app gets its connection from here, so the connection
settings (and the schema) live in exactly one place. Mirrors the Athena pattern.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

# The .sql migration files live next to this module, in migrations/.
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with the settings mnemosyne always wants."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row              # rows act like dicts: row["name"]
    conn.execute("PRAGMA busy_timeout = 5000")  # install the busy handler FIRST, so
    # the lock waits below honor it — the background worker and request handlers write
    # the same file concurrently, and WAL still allows only one writer at a time.
    _enable_wal(conn)
    conn.execute("PRAGMA foreign_keys = ON")    # actually enforce REFERENCES
    return conn


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Put the database in WAL mode, tolerant of a concurrent startup.

    WAL is a PERSISTENT property of the database file, so it only needs to be set
    once — every later connection inherits it. So we read the current mode and only
    switch when it isn't already WAL; a second process booting at the same time then
    skips the switch entirely instead of fighting for it. The switch itself needs a
    brief exclusive moment and can return SQLITE_BUSY ('database is locked') *without*
    honoring the busy timeout when another connection is mid-write (e.g. a sibling
    applying migrations under BEGIN IMMEDIATE), so on the rare first-creation race we
    retry it with a short backoff rather than letting that transient lock crash boot.
    """
    for attempt in range(10):
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() == "wal":
                return
            conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError:
            if attempt == 9:
                raise
            time.sleep(0.05 * (attempt + 1))


def _apply_sql(conn: sqlite3.Connection, sql: str) -> None:
    """Run one migration script inside the caller's transaction."""
    pending: list[str] = []
    for line in sql.splitlines():
        pending.append(line)
        statement = "\n".join(pending).strip()
        if statement and sqlite3.complete_statement(statement):
            conn.execute(statement)
            pending.clear()
    tail = "\n".join(pending).strip()
    if tail:
        conn.execute(tail)


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply every migration that hasn't run yet, in filename order.

    Returns the list of migrations applied this call (empty if already current).
    Safe to run on every startup. The migration check and writes happen under a
    BEGIN IMMEDIATE lock, so multiple app worker processes can start together
    without both trying to apply the same ALTER TABLE.
    """
    applied: list[str] = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        # A table that records which migrations have run — how the runner
        # "remembers" so it doesn't re-apply everything every time.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY,"
            " applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        already = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations")
        }

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = path.name
            if version in already:
                continue
            _apply_sql(conn, path.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
            applied.append(version)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return applied
