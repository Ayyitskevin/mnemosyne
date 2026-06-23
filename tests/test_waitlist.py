"""Tests for the demand-validation waitlist — the data-access rules and the two
public routes. These encode *why* the behaviour matters: a signup is the one
cheap signal we're trusting to decide whether to build the SaaS at all, so it
must dedupe the same person to one row regardless of casing, reject junk at the
boundary, and never lose a valid address. The route tests pin that `/` is the
public pitch (not the local album tool) so we don't accidentally expose the
unauthenticated tool as the front door.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from mnemosyne import db, waitlist
from mnemosyne.main import app, get_conn


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


@pytest.fixture
def client(tmp_path):
    """A TestClient whose DB is a throwaway file, by overriding the get_conn
    dependency. Each test gets a clean schema so signups don't leak between them."""
    db_path = tmp_path / "web.db"
    db.migrate(db.connect(db_path))

    def _conn():
        c = db.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_conn] = _conn
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- data access -----------------------------------------------------------

def test_valid_email_is_stored(conn):
    row = waitlist.add(conn, "kevin@example.com")
    assert row["email"] == "kevin@example.com"
    assert waitlist.count(conn) == 1


def test_email_is_normalized_to_lowercase_and_trimmed(conn):
    row = waitlist.add(conn, "  Kevin@Example.COM  ")
    assert row["email"] == "kevin@example.com"


def test_same_person_different_casing_is_one_row(conn):
    waitlist.add(conn, "kevin@example.com")
    waitlist.add(conn, "KEVIN@example.com")
    # Why it matters: a re-submit must not inflate the only signal we're trusting.
    assert waitlist.count(conn) == 1


def test_add_is_idempotent_and_returns_existing_row(conn):
    first = waitlist.add(conn, "kevin@example.com")
    second = waitlist.add(conn, "kevin@example.com")
    assert first["id"] == second["id"]
    assert waitlist.count(conn) == 1


@pytest.mark.parametrize(
    "good",
    ["a@b.co", "kevin.lee@studio.photography", "x+tag@mail.example.com"],
)
def test_plausible_addresses_pass_validation(good):
    assert waitlist.is_valid_email(good)


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "noatsign", "no@dot", "two@@at.com", "a b@c.com", "@nolocal.com",
     "trailing@dot.", "leading@.dot"],
)
def test_obvious_junk_is_rejected(bad):
    assert not waitlist.is_valid_email(bad)


# --- routes ----------------------------------------------------------------

def test_root_serves_the_public_landing_not_the_album_tool(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "waitlist" in r.text.lower()
    # The album tool lives elsewhere; the front door must not be it.
    assert "drafted albums" not in r.text


def test_albums_index_requires_login(client):
    # The album tool is no longer public: an unauthenticated hit bounces to the
    # sign-in page instead of exposing anyone's galleries.
    r = client.get("/albums")
    assert r.status_code == 200  # followed the redirect
    assert "/login" in str(r.url)
    assert "sign in" in r.text.lower()


def test_post_waitlist_stores_valid_email_and_confirms(client):
    r = client.post("/waitlist", data={"email": "newuser@example.com"})
    assert r.status_code == 200
    assert "on the list" in r.text.lower()


def test_post_waitlist_rejects_junk_without_storing(client, tmp_path):
    r = client.post("/waitlist", data={"email": "not-an-email"})
    assert r.status_code == 200
    assert "look like an email" in r.text.lower()
    # And nothing was written: re-rendered form keeps the bad value for editing.
    assert "not-an-email" in r.text


def test_post_waitlist_dedupes_across_requests(client):
    client.post("/waitlist", data={"email": "dupe@example.com"})
    r = client.post("/waitlist", data={"email": "DUPE@example.com"})
    assert r.status_code == 200
    assert "on the list" in r.text.lower()
