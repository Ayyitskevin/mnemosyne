"""Tests for the SQLite migration runner."""
from __future__ import annotations

import concurrent.futures
import time

from mnemosyne import db


def test_connect_tolerates_an_open_write_lock(tmp_path):
    """Reproduce the concurrent-startup flake deterministically: a second connection
    must open even while another holds a write lock. The old code issued
    `PRAGMA journal_mode = WAL` unconditionally on every connect, which needs an
    exclusive moment and raised 'database is locked' against the held lock; connect
    now reads the mode and skips the switch once WAL is already set, so it doesn't
    fight for that lock."""
    db_path = tmp_path / "app.db"
    holder = db.connect(db_path)            # establishes WAL on the fresh file
    holder.execute("CREATE TABLE t (id INTEGER)")
    holder.commit()
    holder.execute("BEGIN IMMEDIATE")       # hold a writer lock for the whole test
    holder.execute("INSERT INTO t VALUES (1)")
    try:
        started = time.monotonic()
        other = db.connect(db_path)         # must NOT block on / fail against the lock
        elapsed = time.monotonic() - started
        other.close()
        assert elapsed < 2.0, f"connect blocked on the write lock ({elapsed:.2f}s)"
    finally:
        holder.rollback()
        holder.close()


def test_connect_sets_wal_and_foreign_keys(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_migrate_serializes_concurrent_startups(tmp_path, monkeypatch):
    """Two app worker processes can boot at the same time. The loser should wait
    for the winner's migration transaction, then observe the migration as already
    applied instead of trying the same ALTER/CREATE again."""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_slow.sql").write_text(
        "SELECT sleep_ms(200);\n"
        "CREATE TABLE items (id INTEGER PRIMARY KEY);\n"
    )
    (migrations / "0002_add_name.sql").write_text(
        "ALTER TABLE items ADD COLUMN name TEXT;\n"
    )
    monkeypatch.setattr(db, "MIGRATIONS_DIR", migrations)
    db_path = tmp_path / "app.db"

    def run_migrate() -> list[str]:
        conn = db.connect(db_path)
        conn.create_function("sleep_ms", 1, lambda ms: time.sleep(ms / 1000) or 0)
        try:
            return db.migrate(conn)
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_migrate) for _ in range(2)]
        results = [f.result() for f in futures]

    applied = [name for batch in results for name in batch]
    assert sorted(applied) == ["0001_slow.sql", "0002_add_name.sql"]

    conn = db.connect(db_path)
    try:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(items)").fetchall()
        }
        recorded = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
    finally:
        conn.close()
    assert columns == {"id", "name"}
    assert recorded == {"0001_slow.sql", "0002_add_name.sql"}
