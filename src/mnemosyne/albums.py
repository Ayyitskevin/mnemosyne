"""Read side for albums — the queries the web preview needs.

Kept separate from the write path (ingest/vision/arrange/pipeline) so the show
station is a thin reader: it asks here for a fully-assembled album and renders it.
"""
from __future__ import annotations

import sqlite3

from mnemosyne import layout


def list_albums(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT a.*, "
        " (SELECT COUNT(*) FROM photos p WHERE p.album_id = a.id) AS photo_count, "
        " (SELECT COUNT(*) FROM spreads s WHERE s.album_id = a.id) AS spread_count "
        "FROM albums a ORDER BY a.id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_album(conn: sqlite3.Connection, album_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
    return dict(row) if row else None


def get_photo(conn: sqlite3.Connection, photo_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    return dict(row) if row else None


def album_for_render(conn: sqlite3.Connection, album_id: int) -> dict | None:
    """Assemble an album into the shape the template wants: the album, then its
    spreads in order, each carrying its photos in slot order with is_hero set."""
    album = get_album(conn, album_id)
    if album is None:
        return None

    spreads = []
    for s in conn.execute(
        "SELECT * FROM spreads WHERE album_id = ? ORDER BY position", (album_id,)
    ).fetchall():
        spread = dict(s)
        photos = []
        for p in conn.execute(
            "SELECT p.*, pl.slot FROM placements pl "
            "JOIN photos p ON p.id = pl.photo_id "
            "WHERE pl.spread_id = ? ORDER BY pl.slot",
            (spread["id"],),
        ).fetchall():
            photo = dict(p)
            photo["is_hero"] = photo["id"] == spread["hero_photo_id"]
            photo["orientation"] = (
                "portrait" if photo["height"] > photo["width"] else "landscape"
            )
            photos.append(photo)
        spread["photos"] = photos
        spread["layout"] = layout.plan_spread(photos, spread["hero_photo_id"])
        spreads.append(spread)

    return {"album": album, "spreads": spreads}
