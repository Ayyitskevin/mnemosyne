"""Read side for albums — the queries the web preview needs.

Kept separate from the write path (ingest/vision/arrange/pipeline) so the show
station is a thin reader: it asks here for a fully-assembled album and renders it.
"""
from __future__ import annotations

import sqlite3

from mnemosyne import layout


def list_albums(conn: sqlite3.Connection, owner_id: int) -> list[dict]:
    """Albums owned by one user. owner_id is required, not optional — the whole
    point of multi-tenancy is that there is no "list everyone's albums" path."""
    rows = conn.execute(
        "SELECT a.*, "
        " (SELECT COUNT(*) FROM photos p WHERE p.album_id = a.id) AS photo_count, "
        " (SELECT COUNT(*) FROM spreads s WHERE s.album_id = a.id) AS spread_count "
        "FROM albums a WHERE a.owner_id = ? ORDER BY a.id DESC",
        (owner_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_album(
    conn: sqlite3.Connection, album_id: int, owner_id: int | None = None
) -> dict | None:
    """One album. When owner_id is given, a non-owner gets None (indistinguishable
    from "doesn't exist") so the routes can 404 cross-tenant access without leaking
    that the album exists at all."""
    if owner_id is None:
        row = conn.execute(
            "SELECT * FROM albums WHERE id = ?", (album_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM albums WHERE id = ? AND owner_id = ?",
            (album_id, owner_id),
        ).fetchone()
    return dict(row) if row else None


def owns_album(conn: sqlite3.Connection, album_id: int, owner_id: int) -> bool:
    """The guard the write/edit routes call before mutating: does this user own
    this album? Keeps the ownership rule in one place instead of every route."""
    return get_album(conn, album_id, owner_id) is not None


def get_photo(
    conn: sqlite3.Connection, photo_id: int, owner_id: int | None = None
) -> dict | None:
    """One photo. With owner_id set, joins through the album so a user can only
    fetch image files for galleries they own — otherwise someone could guess photo
    ids and pull another tenant's images straight off /photo/<id>."""
    if owner_id is None:
        row = conn.execute(
            "SELECT * FROM photos WHERE id = ?", (photo_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT p.* FROM photos p JOIN albums a ON a.id = p.album_id "
            "WHERE p.id = ? AND a.owner_id = ?",
            (photo_id, owner_id),
        ).fetchone()
    return dict(row) if row else None


def adopt_orphans(conn: sqlite3.Connection, owner_id: int) -> int:
    """Hand every owner-less album (the pre-accounts dogfood data) to one user.
    Returns how many were adopted. Used once by the CLI `adduser` step so the
    first real account inherits the local galleries instead of stranding them."""
    cur = conn.execute(
        "UPDATE albums SET owner_id = ? WHERE owner_id IS NULL", (owner_id,)
    )
    conn.commit()
    return cur.rowcount


def album_for_render(
    conn: sqlite3.Connection, album_id: int, owner_id: int | None = None
) -> dict | None:
    """Assemble an album into the shape the template wants: the album, then its
    spreads in order, each carrying its photos in slot order with is_hero set."""
    album = get_album(conn, album_id, owner_id)
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
