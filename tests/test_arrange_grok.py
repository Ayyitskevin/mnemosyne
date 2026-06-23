"""The arrange step's cloud (grok) backend + selector.

Mirrors the vision-grok contract: the billed reasoning call is OPT-IN (a real
Ollama-less host needs it, but the local dogfood path must never be silently
swapped), the response's JSON-in-prose is sliced+repaired into a complete layout,
and a missing key degrades LOUD to the deterministic fallback rather than crashing
an album build. A successful call surfaces its token usage so COGS gets metered.
The network is mocked — no live key, no real billing in CI.
"""
from __future__ import annotations

import pytest

from mnemosyne import arrange, auth, config, db, usage


_PHOTOS = [
    {"id": 1, "scene": "wide interior", "hero_score": 0.4, "width": 1200, "height": 800},
    {"id": 2, "scene": "hero dish", "hero_score": 0.9, "width": 1200, "height": 800},
    {"id": 3, "scene": "cocktail detail", "hero_score": 0.5, "width": 800, "height": 1200},
]


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

    def post(self, url, json=None, headers=None):
        _FakeClient.captured = {"url": url, "json": json, "headers": headers}
        return _FakeResp(
            {
                "choices": [
                    {"message": {"content": 'sure!\n```json\n'
                                 '{"spreads": [{"photos": [2], "hero": 2}, '
                                 '{"photos": [1, 3], "hero": 3}]}\n```'}}
                ],
                "usage": {"prompt_tokens": 300, "completion_tokens": 40, "total_tokens": 340},
            }
        )


def test_grok_arrange_posts_parses_and_meters(monkeypatch):
    monkeypatch.setattr(config, "XAI_API_KEY", "xai-test")
    monkeypatch.setattr(config, "XAI_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setattr(config, "GROK_ARRANGE_MODEL", "grok-4.20-non-reasoning")
    monkeypatch.setattr(arrange.httpx, "Client", _FakeClient)

    layout, meta = arrange._arrange_via_grok(_PHOTOS)

    # The prose+fence wrapper was sliced and the spreads repaired into a layout.
    assert layout == [{"photos": [2], "hero": 2}, {"photos": [1, 3], "hero": 3}]
    assert _FakeClient.captured["url"] == "https://api.x.ai/v1/chat/completions"
    assert _FakeClient.captured["headers"]["Authorization"] == "Bearer xai-test"
    # Usage bubbles up for metering.
    assert meta["backend"] == "grok"
    assert meta["tokens"]["total_tokens"] == 340


def test_grok_arrange_missing_key_degrades_to_fallback(monkeypatch):
    monkeypatch.setattr(config, "XAI_API_KEY", None)
    layout, meta = arrange._arrange_via_grok(_PHOTOS)
    # No crash — None layout signals the caller to use the deterministic fallback.
    assert layout is None and meta is None


def test_selector_routes_grok_only_when_opted_in(monkeypatch):
    seen = {}
    monkeypatch.setattr(arrange, "_arrange_via_grok", lambda p: seen.setdefault("grok", True) or (None, None))
    monkeypatch.setattr(arrange, "_arrange_via_ollama", lambda p: seen.setdefault("ollama", True) or (None, None))

    monkeypatch.setattr(config, "ARRANGE_BACKEND", "grok")
    arrange._ask_model(_PHOTOS)
    assert seen == {"grok": True}


def test_selector_defaults_to_local(monkeypatch):
    seen = {}
    monkeypatch.setattr(arrange, "_arrange_via_grok", lambda p: seen.setdefault("grok", True) or (None, None))
    monkeypatch.setattr(arrange, "_arrange_via_ollama", lambda p: seen.setdefault("ollama", True) or (None, None))

    monkeypatch.setattr(config, "ARRANGE_BACKEND", None)
    arrange._ask_model(_PHOTOS)
    assert seen == {"ollama": True}


def test_arrange_album_meters_a_grok_call(tmp_path, monkeypatch):
    # End to end through arrange_album: a grok layout call records ONE arrange row.
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    uid = auth.create_user(c, "a@example.com", "pw12345")["id"]
    aid = c.execute(
        "INSERT INTO albums (name, source_dir, owner_id) VALUES ('a', '/x', ?)", (uid,)
    ).lastrowid
    for p in _PHOTOS:
        c.execute(
            "INSERT INTO photos (id, album_id, scene, hero_score, storage_key, width, height) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (p["id"], aid, p["scene"], p["hero_score"], f"a{aid}/{p['id']}.jpg",
             p["width"], p["height"]),
        )
    c.commit()

    monkeypatch.setattr(
        arrange, "_ask_model",
        lambda photos: ([{"photos": [2], "hero": 2}, {"photos": [1, 3], "hero": 3}],
                        {"backend": "grok", "model": "m",
                         "tokens": {"prompt_tokens": 300, "completion_tokens": 40,
                                    "total_tokens": 340}, "latency": 0.7}),
    )
    spreads = arrange.arrange_album(c, aid)
    assert spreads == 2

    s = usage.album_summary(c, aid)
    assert s["calls"] == 1 and s["total_tokens"] == 340
    row = c.execute(
        "SELECT stage, photo_id FROM inference_usage WHERE album_id = ?", (aid,)
    ).fetchone()
    assert row["stage"] == "arrange" and row["photo_id"] is None
