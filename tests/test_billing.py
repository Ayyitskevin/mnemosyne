"""Stripe billing scaffold — feature-flagged, off by default."""
from __future__ import annotations

import json

import pytest

from mnemosyne import auth, billing, config, db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


def test_billing_disabled_by_default(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_ENABLED", False)
    assert billing.billing_enabled() is False
    assert billing.upload_allowed({"billing_status": "canceled"}) is True


def test_upload_blocked_when_billing_on_and_canceled(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_ENABLED", True)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(config, "STRIPE_PRICE_ID", "price_x")
    assert billing.billing_enabled() is True
    assert billing.upload_allowed({"billing_status": "canceled"}) is False
    assert billing.upload_allowed({"billing_status": "active"}) is True


def test_webhook_marks_user_active(conn, monkeypatch):
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    uid = auth.create_user(conn, "bill@example.com", "pw12345")["id"]
    payload = json.dumps(
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "metadata": {"user_id": str(uid)},
                    "customer": "cus_123",
                }
            },
        }
    ).encode()
    import hashlib
    import hmac
    import time

    ts = str(int(time.time()))
    signed = f"{ts}.{payload.decode()}".encode()
    digest = hmac.new(b"whsec_test", signed, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={digest}"
    assert billing.verify_webhook_signature(payload, header)
    billing.handle_webhook(conn, payload)
    row = conn.execute(
        "SELECT billing_status, stripe_customer_id FROM users WHERE id = ?", (uid,)
    ).fetchone()
    assert row["billing_status"] == "active"
    assert row["stripe_customer_id"] == "cus_123"