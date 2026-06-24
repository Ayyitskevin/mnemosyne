"""Tests for the Grok (xAI) cloud vision backend and the backend selector.

These encode *why* the cloud path is shaped the way it is: the per-photo call is
the COGS driver, so (a) only an opt-in env var may route to the billed vendor —
the local dogfood path must never be silently swapped — and (b) a chatty cloud
model's JSON-in-prose reply must still be coerced into a clean, clamped
{scene, hero_score} so the deterministic arrange step downstream never breaks.

No live key is needed: the network call is mocked. One real billed smoke test is
done by hand, not in CI.
"""
from __future__ import annotations

import base64

import pytest
from PIL import Image

from mnemosyne import config, vision


@pytest.fixture
def jpeg(tmp_path):
    """A small real JPEG on disk so _downscale_b64 has something to open."""
    p = tmp_path / "shot.jpg"
    Image.new("RGB", (2000, 1500), (120, 80, 40)).save(p, format="JPEG")
    return str(p)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    """Stands in for httpx.Client; records the request and returns a canned body."""

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
                    {"message": {"content": '```json\n{"scene": "hero plated dish", "hero_score": 0.9}\n```'}}
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 20, "total_tokens": 220},
            }
        )


def test_downscale_caps_longest_side_and_is_valid_jpeg(jpeg):
    b64 = vision._downscale_b64(jpeg, max_px=512)
    import io

    im = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert max(im.size) <= 512
    assert im.format == "JPEG"


def test_parse_strips_markdown_fence_and_clamps():
    out = vision._parse_scene_hero('```json\n{"scene": "x", "hero_score": 5}\n```')
    assert out["scene"] == "x"
    assert out["hero_score"] == 1.0  # clamped from 5 into 0..1


def test_parse_handles_negative_and_garbage_score():
    assert vision._parse_scene_hero('{"scene": "a", "hero_score": -3}')["hero_score"] == 0.0
    assert vision._parse_scene_hero('{"scene": "a", "hero_score": "nope"}')["hero_score"] == 0.0


def test_grok_backend_posts_to_xai_and_parses(jpeg, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "XAI_API_KEY", "xai-test")
    monkeypatch.setattr(config, "XAI_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setattr(config, "GROK_VISION_MODEL", "grok-2-vision-1212")
    monkeypatch.setattr(config, "ROUTING_LOG", tmp_path / "v.log")
    monkeypatch.setattr(vision.httpx, "Client", _FakeClient)

    out = vision._analyze_one_via_grok(jpeg, theme="food")
    assert out["scene"] == "hero plated dish"
    assert out["hero_score"] == 0.9
    # The billed call carries its usage up for metering (look_at_album writes the
    # inference_usage row); local/argus backends omit this key.
    assert out["usage_meta"]["backend"] == "grok"
    assert out["usage_meta"]["tokens"]["total_tokens"] == 220

    cap = _FakeClient.captured
    assert cap["url"] == "https://api.x.ai/v1/chat/completions"
    assert cap["headers"]["Authorization"] == "Bearer xai-test"
    # Image must be sent as a base64 data URL, not a path or the full file.
    content = cap["json"]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # Usage was logged for cost metering.
    assert (tmp_path / "v.log").exists()


def test_grok_backend_requires_key(jpeg, monkeypatch):
    monkeypatch.setattr(config, "XAI_API_KEY", None)
    with pytest.raises(RuntimeError, match="XAI_API_KEY"):
        vision._analyze_one_via_grok(jpeg, theme="food")


def test_selector_routes_grok_only_when_opted_in(jpeg, monkeypatch):
    calls = {}
    monkeypatch.setattr(
        vision, "_analyze_one_via_grok", lambda p, **kw: calls.setdefault("grok", p)
    )
    monkeypatch.setattr(
        vision, "_analyze_one_via_ollama", lambda p, **kw: calls.setdefault("ollama", p)
    )
    monkeypatch.setattr(
        vision, "_analyze_one_via_argus", lambda p, **kw: calls.setdefault("argus", p)
    )

    # Explicit grok.
    monkeypatch.setattr(config, "VISION_BACKEND", "grok")
    vision.analyze_one(jpeg)
    assert "grok" in calls and "ollama" not in calls


def test_selector_defaults_to_ollama_not_grok(jpeg, monkeypatch):
    calls = {}
    monkeypatch.setattr(
        vision, "_analyze_one_via_grok", lambda p, **kw: calls.setdefault("grok", p)
    )
    monkeypatch.setattr(
        vision, "_analyze_one_via_ollama", lambda p, **kw: calls.setdefault("ollama", p)
    )
    monkeypatch.setattr(
        vision, "_analyze_one_via_argus", lambda p, **kw: calls.setdefault("argus", p)
    )

    monkeypatch.setattr(config, "VISION_BACKEND", None)
    monkeypatch.setattr(config, "ARGUS_URL", None)
    vision.analyze_one(jpeg)
    assert "ollama" in calls and "grok" not in calls


def test_selector_defaults_to_argus_when_url_set(jpeg, monkeypatch):
    calls = {}
    monkeypatch.setattr(
        vision, "_analyze_one_via_grok", lambda p, **kw: calls.setdefault("grok", p)
    )
    monkeypatch.setattr(
        vision, "_analyze_one_via_ollama", lambda p, **kw: calls.setdefault("ollama", p)
    )
    monkeypatch.setattr(
        vision, "_analyze_one_via_argus", lambda p, **kw: calls.setdefault("argus", p)
    )

    monkeypatch.setattr(config, "VISION_BACKEND", None)
    monkeypatch.setattr(config, "ARGUS_URL", "http://mickey:8010")
    vision.analyze_one(jpeg)
    assert "argus" in calls and "grok" not in calls
