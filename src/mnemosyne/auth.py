"""Auth — password hashing and the user read/write side.

Two jobs, kept apart from the routes that call them: turn a password into a
verifiable hash (and back-check one), and create/look up user rows. Hashing is
stdlib pbkdf2-HMAC-SHA256 — no third-party crypto dependency to carry, and the
cost factor is tunable. The hash is stored self-describing so a future cost bump
needs no migration: existing rows keep verifying at their old cost, new rows use
the new one.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from mnemosyne import config
from mnemosyne.waitlist import is_valid_email, normalize_email

# OWASP's floor for pbkdf2-HMAC-SHA256 is ~600k iterations (2023+). Stored inside
# each hash, so raising this only affects accounts created or re-hashed afterward.
_ITERATIONS = 600_000
_ALGO = "pbkdf2_sha256"


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    """Hash a password into "pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>".

    A fresh random salt per call means two users with the same password get
    different hashes, so the stored values leak nothing about shared passwords.
    """
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{_ALGO}${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash. Re-derives with the salt + cost
    baked into the stored string and compares in constant time (hmac.compare_digest)
    so a timing side-channel can't probe the hash byte by byte."""
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iters)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def create_user(conn: sqlite3.Connection, email: str, password: str) -> dict:
    """Create an account. Returns the stored row (without the hash).

    Caller is the boundary that should have already validated input, but we guard
    here too (defense in depth on the one table that gates everything). Raises
    ValueError on a bad/empty email or a duplicate — the route turns those into a
    friendly re-render rather than a 500.
    """
    email = normalize_email(email)
    if not is_valid_email(email):
        raise ValueError("invalid email")
    if not password:
        raise ValueError("empty password")
    if get_user_by_email(conn, email) is not None:
        raise ValueError("email already registered")
    cur = conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        (email, hash_password(password)),
    )
    conn.commit()
    return {"id": cur.lastrowid, "email": email}


def get_user_by_email(conn: sqlite3.Connection, email: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM users WHERE email = ?", (normalize_email(email),)
    ).fetchone()
    return dict(row) if row else None


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def request_password_reset(conn: sqlite3.Connection, email: str) -> str | None:
    """Create a reset token for a registered email. Returns the raw token when
    the user exists (caller handles delivery); None when unknown — same outward UX."""
    user = get_user_by_email(conn, email)
    if user is None:
        return None
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=config.RESET_TOKEN_TTL_HOURS)
    conn.execute(
        "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
        (user["id"], _hash_token(raw), expires.isoformat()),
    )
    conn.commit()
    return raw


def reset_password(conn: sqlite3.Connection, token: str, new_password: str) -> bool:
    if not new_password:
        return False
    row = conn.execute(
        "SELECT id, user_id, expires_at, used_at FROM password_reset_tokens "
        "WHERE token_hash = ?",
        (_hash_token(token),),
    ).fetchone()
    if row is None or row["used_at"]:
        return False
    expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        return False
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(new_password), row["user_id"]),
    )
    conn.execute(
        "UPDATE password_reset_tokens SET used_at = datetime('now') WHERE id = ?",
        (row["id"],),
    )
    conn.commit()
    return True


def delete_user(conn: sqlite3.Connection, user_id: int, delete_albums_fn) -> bool:
    """Delete every album then the user row. delete_albums_fn is pipeline.delete_album."""
    rows = conn.execute(
        "SELECT id, status FROM albums WHERE owner_id = ?", (user_id,)
    ).fetchall()
    for row in rows:
        if row["status"] in {"pending", "processing"}:
            return False
    for row in rows:
        delete_albums_fn(conn, row["id"])
    cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    return cur.rowcount == 1


# A real hash of a throwaway secret, verified against when no user matches so the
# unknown-email branch pays the same pbkdf2 cost as a wrong-password one. The
# password it represents is never knowable, so this can never accidentally pass.
_DECOY_HASH = hash_password(secrets.token_hex(16))


def authenticate(conn: sqlite3.Connection, email: str, password: str) -> dict | None:
    """Return the user row if email+password check out, else None.

    Deliberately one outcome for "no such email" and "wrong password" — and one
    timing profile too: a missing user still runs verify_password against a decoy
    hash, so response latency can't tell an attacker which emails have accounts.
    """
    user = get_user_by_email(conn, email)
    stored = user["password_hash"] if user is not None else _DECOY_HASH
    ok = verify_password(password, stored)
    if user is None or not ok:
        return None
    return user
