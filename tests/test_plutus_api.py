"""Plutus API client — mint offer URLs."""
from __future__ import annotations

import pytest

from mnemosyne import config, plutus_api


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    captured = {}

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, data=None, headers=None):
        _FakeClient.captured = {"url": url, "data": data, "headers": headers}
        return _FakeResp(
            {
                "public_url": "https://plutus.example.com/store/demo/offer/tok123",
                "token": "tok123",
            }
        )


def test_create_offer_url_calls_plutus(monkeypatch):
    monkeypatch.setattr(config, "PLUTUS_URL", "https://plutus.example.com")
    monkeypatch.setattr(config, "PLUTUS_API_TOKEN", "tok")
    monkeypatch.setattr(config, "PLUTUS_TENANT_ID", "flow-studio")
    monkeypatch.setattr(plutus_api.httpx, "Client", _FakeClient)
    url = plutus_api.create_offer_url(run_id=42, label="My album")
    assert url == "https://plutus.example.com/store/demo/offer/tok123"
    assert _FakeClient.captured["url"].endswith("/integrations/offer")
    assert _FakeClient.captured["data"]["run_id"] == "42"
    assert _FakeClient.captured["data"]["tenant_id"] == "flow-studio"


def test_create_offer_url_requires_config(monkeypatch):
    monkeypatch.setattr(config, "PLUTUS_API_TOKEN", None)
    with pytest.raises(plutus_api.PlutusApiError):
        plutus_api.create_offer_url(run_id=1)