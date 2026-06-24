"""Stripe billing — per-photographer subscriptions (feature-flagged)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sqlite3
import time
from typing import Any

import httpx2 as httpx

from mnemosyne import config

log = logging.getLogger("mnemosyne.billing")

STRIPE_API = "https://api.stripe.com/v1"


class BillingError(Exception):
    pass


def _stripe_value_ok(value: str | None) -> bool:
    if not value or not str(value).strip():
        return False
    return "CHANGE_ME" not in str(value).upper()


def billing_enabled() -> bool:
    return (
        config.STRIPE_ENABLED
        and _stripe_value_ok(config.STRIPE_SECRET_KEY)
        and _stripe_value_ok(config.STRIPE_PRICE_ID)
    )


def upload_allowed(user: dict | None) -> bool:
    """When billing is off, everyone uploads. When on, trialing/active may upload."""
    if not billing_enabled() or user is None:
        return True
    status = (user.get("billing_status") or "trialing").strip().lower()
    return status in {"trialing", "active"}


def billing_view(user: dict | None) -> dict[str, Any]:
    if user is None:
        return {"enabled": billing_enabled(), "status": "none"}
    status = user.get("billing_status") or "trialing"
    active = status == "active"
    return {
        "enabled": billing_enabled(),
        "status": status,
        "is_active": active,
        "can_subscribe": billing_enabled() and not active,
        "can_manage": billing_enabled() and bool(user.get("stripe_customer_id")),
        "customer_linked": bool(user.get("stripe_customer_id")),
    }


def _stripe_request(method: str, path: str, data: dict | None = None) -> dict:
    if not config.STRIPE_SECRET_KEY:
        raise BillingError("STRIPE_SECRET_KEY is not set")
    url = f"{STRIPE_API}{path}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.request(
            method, url, data=data, auth=(config.STRIPE_SECRET_KEY, "")
        )
    if resp.status_code >= 400:
        raise BillingError(f"Stripe HTTP {resp.status_code}: {resp.text[:400]}")
    return resp.json()


def ensure_customer(conn: sqlite3.Connection, user: dict) -> str:
    existing = user.get("stripe_customer_id")
    if existing:
        return existing
    body = {
        "email": user["email"],
        "metadata[user_id]": str(user["id"]),
    }
    customer = _stripe_request("POST", "/customers", body)
    customer_id = customer["id"]
    conn.execute(
        "UPDATE users SET stripe_customer_id = ? WHERE id = ? AND stripe_customer_id IS NULL",
        (customer_id, user["id"]),
    )
    conn.commit()
    row = conn.execute(
        "SELECT stripe_customer_id FROM users WHERE id = ?", (user["id"],)
    ).fetchone()
    return row["stripe_customer_id"] or customer_id


def create_checkout_session(conn: sqlite3.Connection, user: dict) -> dict:
    if not billing_enabled():
        raise BillingError("billing not configured")
    customer_id = ensure_customer(conn, user)
    session = _stripe_request(
        "POST",
        "/checkout/sessions",
        {
            "mode": "subscription",
            "customer": customer_id,
            "line_items[0][price]": config.STRIPE_PRICE_ID,
            "line_items[0][quantity]": "1",
            "success_url": config.STRIPE_SUCCESS_URL,
            "cancel_url": config.STRIPE_CANCEL_URL,
            "metadata[user_id]": str(user["id"]),
            "subscription_data[metadata][user_id]": str(user["id"]),
        },
    )
    return {"checkout_url": session["url"], "session_id": session["id"]}


def create_portal_session(conn: sqlite3.Connection, user: dict) -> dict:
    if not billing_enabled():
        raise BillingError("billing not configured")
    customer_id = ensure_customer(conn, user)
    portal = _stripe_request(
        "POST",
        "/billing_portal/sessions",
        {
            "customer": customer_id,
            "return_url": config.STRIPE_PORTAL_RETURN_URL,
        },
    )
    return {"portal_url": portal["url"]}


def verify_webhook_signature(payload: bytes, sig_header: str | None) -> bool:
    secret = config.STRIPE_WEBHOOK_SECRET
    if not secret or not sig_header:
        return False
    parts: dict[str, str] = {}
    for item in sig_header.split(","):
        key, _, value = item.partition("=")
        parts[key] = value
    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        return False
    signed = f"{timestamp}.{payload.decode()}".encode()
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle_webhook(conn: sqlite3.Connection, payload: bytes) -> None:
    event = json.loads(payload)
    etype = event.get("type")
    obj = event.get("data", {}).get("object", {})

    if etype == "checkout.session.completed":
        user_id = (obj.get("metadata") or {}).get("user_id")
        customer_id = obj.get("customer")
        if user_id:
            conn.execute(
                "UPDATE users SET billing_status = 'active', stripe_customer_id = COALESCE(stripe_customer_id, ?) "
                "WHERE id = ?",
                (customer_id, int(user_id)),
            )
            conn.commit()
        return

    if etype in {"customer.subscription.deleted", "customer.subscription.updated"}:
        status = (obj.get("status") or "").lower()
        customer_id = obj.get("customer")
        mapped = "active" if status == "active" else "canceled" if status in {
            "canceled",
            "unpaid",
            "past_due",
        } else status or "canceled"
        if customer_id:
            conn.execute(
                "UPDATE users SET billing_status = ? WHERE stripe_customer_id = ?",
                (mapped, customer_id),
            )
            conn.commit()