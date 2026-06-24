"""Runtime status — which backends and storage are active."""
from __future__ import annotations

from mnemosyne import config


def vision_backend() -> str:
    backend = (config.VISION_BACKEND or "").strip().lower()
    if backend == "grok":
        return "grok"
    if backend == "ollama":
        return "ollama"
    if backend == "argus" or (not backend and config.ARGUS_URL):
        return "argus"
    return "ollama"


def arrange_backend() -> str:
    if (config.ARRANGE_BACKEND or "").strip().lower() == "grok":
        return "grok"
    return "ollama"


def backend_status() -> dict[str, str | bool]:
    """Summary for /healthz and the albums dashboard."""
    vision = vision_backend()
    arrange = arrange_backend()
    return {
        "vision": vision,
        "vision_model": _vision_model_label(vision),
        "arrange": arrange,
        "arrange_model": _arrange_model_label(arrange),
        "storage": config.STORAGE_BACKEND,
        "public_url": bool(config.PUBLIC_URL),
        "plutus_url": bool(config.PLUTUS_URL),
        "grok_priced": bool(
            config.GROK_PRICE_PROMPT_PER_M or config.GROK_PRICE_COMPLETION_PER_M
        ),
    }


def _vision_model_label(backend: str) -> str:
    if backend == "grok":
        return config.GROK_VISION_MODEL
    if backend == "argus":
        return config.ARGUS_URL or "argus"
    return config.VISION_MODEL


def _arrange_model_label(backend: str) -> str:
    if backend == "grok":
        return config.GROK_ARRANGE_MODEL
    return config.ARRANGE_MODEL