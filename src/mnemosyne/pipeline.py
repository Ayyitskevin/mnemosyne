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
    """Run a folder all the way through to a laid-out album. Returns a small
    summary (album id + how many photos were analyzed + how many spreads)."""
    album_id = ingest.ingest_folder(
        conn, name=name, source_dir=source_dir, owner_id=owner_id
    )
    looked = vision.look_at_album(conn, album_id)
    spreads = arrange.arrange_album(conn, album_id)
    return {"album_id": album_id, "looked": looked, "spreads": spreads}
