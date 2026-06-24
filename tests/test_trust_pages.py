"""Trust surface — privacy policy and terms pages (public, no auth)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from mnemosyne.main import app


def test_privacy_page_is_public_and_states_no_training():
    r = TestClient(app).get("/privacy")
    assert r.status_code == 200
    assert "do not train" in r.text.lower() or "do not fine-tune" in r.text.lower()
    assert "tenant" in r.text.lower() or "isolation" in r.text.lower()


def test_terms_page_is_public_and_links_privacy():
    r = TestClient(app).get("/terms")
    assert r.status_code == 200
    assert "terms of service" in r.text.lower()
    assert 'href="/privacy"' in r.text


def test_landing_footer_links_trust_pages():
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert 'href="/signup"' in r.text
    assert 'href="/login"' in r.text
    assert 'href="/privacy"' in r.text
    assert 'href="/terms"' in r.text


def test_signup_mentions_terms():
    r = TestClient(app).get("/signup")
    assert r.status_code == 200
    assert 'href="/terms"' in r.text
    assert 'href="/privacy"' in r.text