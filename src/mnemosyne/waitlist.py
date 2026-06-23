"""Waitlist — the read/write side of the demand-validation signup.

Deliberately tiny: a normalize step, a sanity check, an idempotent insert, and a
count. Email *normalization* (so two casings of the same address collapse to one
row) lives here because it's the canonical form the UNIQUE constraint depends on;
deciding whether a string is plausibly an address is the boundary's job and stays
a light sanity check, not a full RFC validator.
"""
from __future__ import annotations

import sqlite3


def normalize_email(email: str) -> str:
    """The canonical form we store and dedupe on: trimmed and lowercased.

    Addresses are case-insensitive in the part that matters for delivery, and
    nobody types their email the same way twice. Collapsing here means the
    UNIQUE(email) constraint actually catches "Kevin@X.com" and "kevin@x.com"
    as the same person.
    """
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    """A light plausibility check, not a guarantee of deliverability.

    We only reject the obviously-broken (no @, no dot in the domain, whitespace
    in the middle) so the table isn't polluted by typos and bot junk. Real
    confirmation is a job for a later double-opt-in email, not a regex.
    """
    email = email.strip()
    if not email or " " in email or email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


def add(conn: sqlite3.Connection, email: str, source: str | None = None) -> dict:
    """Add an email to the waitlist, idempotently. Returns the stored row.

    INSERT OR IGNORE leans on the UNIQUE constraint: a repeat signup quietly hits
    the existing row instead of erroring or duplicating, so the landing page can
    always show "you're on the list" without caring whether it's the first time.
    The caller is expected to have validated the address already (route boundary).
    """
    email = normalize_email(email)
    conn.execute(
        "INSERT OR IGNORE INTO waitlist (email, source) VALUES (?, ?)",
        (email, source),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM waitlist WHERE email = ?", (email,)
    ).fetchone()
    return dict(row)


def count(conn: sqlite3.Connection) -> int:
    """How many people are on the list. Used for ops/curiosity, not shown on the
    page — a public "0 signups" counter only discourages the first signup."""
    return conn.execute("SELECT COUNT(*) AS n FROM waitlist").fetchone()["n"]
