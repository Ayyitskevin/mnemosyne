"""Share links — hand a finished album to someone with no account.

Two sides, deliberately apart: an owner-only mint/revoke on the write path, and a
public read resolver. The token IS the authorization — holding the link is the
capability — so the private album_id never rides in a public URL, and the link
self-closes at share_expires_at (a forwarded or leaked link stops working on its
own, no revoke needed). Every public read funnels through resolve_token so the
"ready + not expired" check lives in exactly one place; a route can't forget half
of it. Mint/revoke are owner-scoped in SQL, mirroring the edits.py write path —
the album_id alone is never enough, it must be paired with the owning user.
"""
from __future__ import annotations

import secrets
import sqlite3

from mnemosyne import config


def create_link(
    conn: sqlite3.Connection, album_id: int, owner_id: int, ttl_days: int | None = None
) -> dict | None:
    """Mint (or refresh) a share link for an owned album, expiring `ttl_days` out
    (config default). Returns {token, expires_at}, or None if the album isn't the
    user's — the owner_id in the WHERE means a guessed album_id can't be shared by a
    stranger. Re-minting rotates the token, which silently invalidates any link
    already in the wild — that's the 'start fresh' affordance."""
    if ttl_days is None:
        ttl_days = config.SHARE_LINK_TTL_DAYS
    token = secrets.token_urlsafe(24)
    cur = conn.execute(
        "UPDATE albums SET share_token = ?, share_expires_at = datetime('now', ?) "
        "WHERE id = ? AND owner_id = ?",
        (token, f"+{int(ttl_days)} days", album_id, owner_id),
    )
    conn.commit()
    if cur.rowcount != 1:
        return None
    row = conn.execute(
        "SELECT share_token, share_expires_at FROM albums WHERE id = ?", (album_id,)
    ).fetchone()
    return {"token": row["share_token"], "expires_at": row["share_expires_at"]}


def revoke_link(conn: sqlite3.Connection, album_id: int, owner_id: int) -> bool:
    """Kill an owned album's share link now. Returns True if a live link was
    cleared, False if there was nothing to revoke or the album isn't the user's."""
    cur = conn.execute(
        "UPDATE albums SET share_token = NULL, share_expires_at = NULL "
        "WHERE id = ? AND owner_id = ? AND share_token IS NOT NULL",
        (album_id, owner_id),
    )
    conn.commit()
    return cur.rowcount == 1


def resolve_token(conn: sqlite3.Connection, token: str | None) -> int | None:
    """The public gate: the album_id a live token unlocks, or None. Requires the
    token to match, the album to be 'ready' (a still-processing album has nothing to
    show), and the link to be unexpired — all three in one query so no caller can
    check the token but forget the clock."""
    if not token:
        return None
    row = conn.execute(
        "SELECT id FROM albums WHERE share_token = ? AND status = 'ready' "
        "AND share_expires_at > datetime('now')",
        (token,),
    ).fetchone()
    return row["id"] if row else None


def shared_photo_key(
    conn: sqlite3.Connection, token: str | None, photo_id: int
) -> str | None:
    """Storage key for a photo a share viewer is allowed to load, or None. Scopes
    the photo to the token's own album so a viewer can't pair a valid token with a
    guessed photo_id from someone else's gallery — the share analogue of the
    owner-scoped /photo route's tenant check."""
    album_id = resolve_token(conn, token)
    if album_id is None:
        return None
    row = conn.execute(
        "SELECT storage_key FROM photos WHERE id = ? AND album_id = ?",
        (photo_id, album_id),
    ).fetchone()
    return row["storage_key"] if row else None
