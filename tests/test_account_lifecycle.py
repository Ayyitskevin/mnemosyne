"""Account delete and password reset."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mnemosyne import auth, config, db, pipeline
from mnemosyne.main import app, get_conn


@pytest.fixture
def web(tmp_path, monkeypatch):
    db_path = tmp_path / "web.db"
    db.migrate(db.connect(db_path))
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "DEV_RESET_LINKS", True)

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


def test_password_reset_round_trip(web):
    client, db_path = web
    client.post("/signup", data={"email": "u@example.com", "password": "oldpw12"})
    c = db.connect(db_path)
    token = auth.request_password_reset(c, "u@example.com")
    assert token
    c.close()
    r2 = client.post(
        "/reset-password",
        data={"token": token, "password": "newpw123"},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/login"
    c = db.connect(db_path)
    user = auth.authenticate(c, "u@example.com", "newpw123")
    assert user is not None
    c.close()


def test_delete_account_requires_password(web):
    client, _ = web
    client.post("/signup", data={"email": "d@example.com", "password": "secret12"})
    client.post("/login", data={"email": "d@example.com", "password": "secret12"})
    r = client.post("/account/delete", data={"password": "wrong"})
    assert r.status_code == 200
    assert b"incorrect" in r.content.lower()
    r2 = client.post(
        "/account/delete", data={"password": "secret12"}, follow_redirects=False
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/"
    assert client.get("/albums", follow_redirects=False).status_code == 303