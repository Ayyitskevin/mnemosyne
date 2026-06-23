"""Database access for mnemosyne.

One SQLite file, opened in WAL mode, with a tiny forward-only migration runner.
Every other part of the app gets its connection from here, so the connection
settings (and the schema) live in exactly one place. Mirrors the Athena pattern.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# The .sql migration files live next to this module, in migrations/.
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with the settings mnemosyne always wants."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row              # rows act like dicts: row["path"]
    conn.execute("PRAGMA journal_mode = WAL")   # readers don't block the writer
    conn.execute("PRAGMA foreign_keys = ON")    # actually enforce REFERENCES
    conn.execute("PRAGMA busy_timeout = 5000")  # wait up to 5s for a lock instead
    # of erroring — the background worker and request handlers now write the same
    # file concurrently, and WAL still allows only one writer at a time.
    return conn


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
