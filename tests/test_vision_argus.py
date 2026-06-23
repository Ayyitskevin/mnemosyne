"""Argus delegation — path vs multipart upload (mock HTTP only)."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from mnemosyne import config, vision


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, data=None, files=None, headers=None):
        _FakeClient.captured = {
            "url": url,
            "data": data,
            "files": files,
            "headers": headers or {},
        }
        return _FakeResp(
            {
                "shot_type": "hero",
                "keywords": ["plating"],
                "culling": {"hero_potential": 0.82},
            }
        )


@pytest.fixture
def jpeg(tmp_path):
    p = tmp_path / "shot.jpg"
    Image.new("RGB", (400, 300), (90, 60, 30)).save(p, format="JPEG")
    return str(p)


def test_argus_path_delegation(jpeg, monkeypatch):
    monkeypatch.setattr(config, "ARGUS_URL", "http://mickey:8010")
    monkeypatch.setattr(config, "ARGUS_API_TOKEN", "argus-secret")
    monkeypatch.setattr(config, "ARGUS_DELEGATION_MODE", "path")
    monkeypatch.setattr(vision.httpx, "Client", _FakeClient)

    out = vision._analyze_one_via_argus(jpeg)
    assert out["scene"].startswith("hero")
    assert out["hero_score"] == 0.82

    cap = _FakeClient.captured
    assert cap["url"] == "http://mickey:8010/analyze"
    assert cap["data"] == {"path": jpeg}
    assert cap["files"] is None
    assert cap["headers"]["Authorization"] == "Bearer argus-secret"


def test_argus_upload_delegation(jpeg, monkeypatch):
    monkeypatch.setattr(config, "ARGUS_URL", "http://mickey:8010")
    monkeypatch.setattr(config, "ARGUS_API_TOKEN", "argus-secret")
    monkeypatch.setattr(config, "ARGUS_DELEGATION_MODE", "upload")
    monkeypatch.setattr(vision.httpx, "Client", _FakeClient)

    vision._analyze_one_via_argus(jpeg)
    cap = _FakeClient.captured
    assert cap["data"] is None
    assert cap["files"] is not None
    name, _fh, mime = cap["files"]["file"]
    assert name == "shot.jpg"
    assert mime == "image/jpeg"


def test_argus_maps_error_shape_to_fallback(jpeg, monkeypatch):
    monkeypatch.setattr(config, "ARGUS_URL", "http://mickey:8010")
    monkeypatch.setattr(config, "ARGUS_DELEGATION_MODE", "path")

    class _ErrClient(_FakeClient):
        def post(self, *a, **k):
            return _FakeResp({"error": "nope"})

    monkeypatch.setattr(vision.httpx, "Client", _ErrClient)
    out = vision._analyze_one_via_argus(jpeg)
    assert out == {"scene": "other", "hero_score": 0.5}