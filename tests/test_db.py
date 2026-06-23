"""Tests for the SQLite migration runner."""
from __future__ import annotations

import concurrent.futures
import time

from mnemosyne import db


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
