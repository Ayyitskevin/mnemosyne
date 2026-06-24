"""Runtime backend status labels."""
from __future__ import annotations

from mnemosyne import config, runtime


def test_default_backends_are_local(monkeypatch):
    monkeypatch.delenv("MNEMOSYNE_VISION_BACKEND", raising=False)
    monkeypatch.delenv("MNEMOSYNE_ARRANGE_BACKEND", raising=False)
    monkeypatch.setattr(config, "VISION_BACKEND", None)
    monkeypatch.setattr(config, "ARRANGE_BACKEND", None)
    monkeypatch.setattr(config, "ARGUS_URL", None)
    status = runtime.backend_status()
    assert status["vision"] == "ollama"
    assert status["arrange"] == "ollama"
    assert status["storage"] == "local"


def test_grok_backends_when_configured(monkeypatch):
    monkeypatch.setattr(config, "VISION_BACKEND", "grok")
    monkeypatch.setattr(config, "ARRANGE_BACKEND", "grok")
    monkeypatch.setattr(config, "GROK_PRICE_PROMPT_PER_M", 2.0)
    status = runtime.backend_status()
    assert status["vision"] == "grok"
    assert status["arrange"] == "grok"
    assert status["grok_priced"] is True