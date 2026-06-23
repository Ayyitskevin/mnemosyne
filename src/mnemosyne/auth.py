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


def authenticate(conn: sqlite3.Connection, email: str, password: str) -> dict | None:
    """Return the user row if email+password check out, else None.

    Deliberately one outcome for "no such email" and "wrong password" — telling
    them apart would let someone enumerate which emails have accounts.
    """
    user = get_user_by_email(conn, email)
    if user is None or not verify_password(password, user["password_hash"]):
        return None
    return user
