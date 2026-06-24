"""Tests for accounts and multi-tenancy — the rules that let strangers share one
mnemosyne without seeing each other's work. These encode *why* each behaviour
matters: a password must never be stored recoverably, login must not reveal which
emails are registered, and the cardinal SaaS invariant — one tenant can never read
another's galleries — must hold at the route layer, where a forgotten owner check
would leak real customer photos.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from mnemosyne import auth, db
from mnemosyne.main import app, get_conn


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


@pytest.fixture
def web(tmp_path):
    """A TestClient plus a handle to its DB, so a test can seed rows directly and
    then drive the same database through HTTP. Cookies persist on the client, so a
    POST /login leaves the session authenticated for later requests."""
    db_path = tmp_path / "web.db"
    db.migrate(db.connect(db_path))

    def _conn():
        c = db.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_conn] = _conn
    client = TestClient(app)
    yield client, db_path
    app.dependency_overrides.clear()


def _seed_album(db_path, owner_id: int, name: str) -> int:
    c = db.connect(db_path)
    cur = c.execute(
        "INSERT INTO albums (name, source_dir, owner_id) VALUES (?, ?, ?)",
        (name, "/tmp/" + name, owner_id),
    )
    c.commit()
    aid = cur.lastrowid
    c.close()
    return aid


# --- password hashing ------------------------------------------------------

def test_hash_verifies_and_is_not_reversible():
    stored = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", stored)
    # The plaintext must not be recoverable from what we persist.
    assert "correct horse" not in stored


def test_wrong_password_is_rejected():
    stored = auth.hash_password("hunter2")
    assert not auth.verify_password("hunter3", stored)


def test_same_password_gets_distinct_hashes():
    # Per-call salt: two users with the same password must not collide, or a
    # breach would reveal shared passwords across accounts.
    assert auth.hash_password("same") != auth.hash_password("same")


def test_garbage_stored_hash_fails_closed():
    assert not auth.verify_password("anything", "not-a-real-hash")


# --- user creation ---------------------------------------------------------

def test_create_user_then_authenticate(conn):
    auth.create_user(conn, "a@example.com", "pw12345")
    user = auth.authenticate(conn, "a@example.com", "pw12345")
    assert user is not None and user["email"] == "a@example.com"


def test_authenticate_is_ambiguous_about_missing_vs_wrong(conn):
    auth.create_user(conn, "a@example.com", "pw12345")
    # Both must be the same outcome (None) so an attacker can't enumerate emails.
    assert auth.authenticate(conn, "a@example.com", "wrong") is None
    assert auth.authenticate(conn, "nobody@example.com", "pw12345") is None


def test_authenticate_hashes_even_when_email_missing(conn, monkeypatch):
    """The ambiguity above must hold in TIMING too: a login for an unknown email
    still runs the (expensive) password verification against a decoy hash. If it
    short-circuited, the latency gap would itself leak which emails are registered.
    """
    calls = []
    real_verify = auth.verify_password

    def spy(password, stored):
        calls.append(stored)
        return real_verify(password, stored)

    monkeypatch.setattr(auth, "verify_password", spy)
    assert auth.authenticate(conn, "nobody@example.com", "pw12345") is None
    # verify ran exactly once, against the decoy — not skipped for the missing user.
    assert calls == [auth._DECOY_HASH]


def test_duplicate_email_rejected(conn):
    auth.create_user(conn, "a@example.com", "pw12345")
    with pytest.raises(ValueError):
        auth.create_user(conn, "A@example.com", "other")


def test_invalid_email_rejected(conn):
    with pytest.raises(ValueError):
        auth.create_user(conn, "not-an-email", "pw12345")


# --- web flow --------------------------------------------------------------

def test_signup_logs_in_and_shows_own_albums(web):
    client, _ = web
    r = client.post(
        "/signup", data={"email": "new@example.com", "password": "pw12345"}
    )
    assert r.status_code == 200 and "/albums" in str(r.url)
    # Session is live: a follow-up to the gated index renders, not redirects.
    assert "drafted albums" in r.text


def test_logout_drops_the_session(web):
    client, _ = web
    client.post("/signup", data={"email": "new@example.com", "password": "pw12345"})
    client.post("/logout")
    r = client.get("/albums")
    assert "/login" in str(r.url)


def test_login_bad_password_does_not_authenticate(web):
    client, _ = web
    client.post("/signup", data={"email": "new@example.com", "password": "right"})
    client.post("/logout")
    r = client.post(
        "/login", data={"email": "new@example.com", "password": "wrong"}
    )
    assert "incorrect" in r.text.lower()
    # Still not logged in.
    assert "/login" in str(client.get("/albums").url)


# --- the cardinal invariant: tenant isolation ------------------------------

def test_user_sees_only_their_own_albums(web):
    client, db_path = web
    a = auth.create_user(db.connect(db_path), "a@example.com", "pw12345")
    b = auth.create_user(db.connect(db_path), "b@example.com", "pw12345")
    _seed_album(db_path, a["id"], "alice-album")
    _seed_album(db_path, b["id"], "bob-album")

    client.post("/login", data={"email": "a@example.com", "password": "pw12345"})
    r = client.get("/albums")
    assert "alice-album" in r.text
    assert "bob-album" not in r.text


def test_cross_tenant_album_is_404_not_403(web):
    client, db_path = web
    auth.create_user(db.connect(db_path), "a@example.com", "pw12345")
    b = auth.create_user(db.connect(db_path), "b@example.com", "pw12345")
    bob_album = _seed_album(db_path, b["id"], "bob-album")

    client.post("/login", data={"email": "a@example.com", "password": "pw12345"})
    r = client.get(f"/albums/{bob_album}")
    # 404 (not 403): a stranger probing ids can't tell "not yours" from "no such".
    assert r.status_code == 404


def test_cross_tenant_pdf_and_edits_blocked(web):
    client, db_path = web
    auth.create_user(db.connect(db_path), "a@example.com", "pw12345")
    b = auth.create_user(db.connect(db_path), "b@example.com", "pw12345")
    bob_album = _seed_album(db_path, b["id"], "bob-album")

    client.post("/login", data={"email": "a@example.com", "password": "pw12345"})
    assert client.get(f"/albums/{bob_album}/pdf").status_code == 404
    r = client.post(
        f"/albums/{bob_album}/spreads/1/move/up", follow_redirects=False
    )
    assert r.status_code == 404
