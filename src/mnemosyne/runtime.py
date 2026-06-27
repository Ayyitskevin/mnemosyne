"""Runtime status — which backends and storage are active."""
from __future__ import annotations

import os

from mnemosyne import config, storage


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
    backend = (config.ARRANGE_BACKEND or "").strip().lower()
    if backend == "grok":
        return "grok"
    if backend == "deterministic":
        return "deterministic"
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
        "mise_import": bool(config.MISE_URL and config.MISE_API_TOKEN),
        "plutus_auto_link": config.PLUTUS_AUTO_LINK,
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
    if backend == "deterministic":
        return "deterministic-v1"
    return config.ARRANGE_MODEL


def storage_status() -> dict[str, str | bool]:
    """Probe the active storage driver — used by /healthz and wire scripts."""
    backend = (config.STORAGE_BACKEND or "local").strip().lower()
    try:
        store = storage.get_storage()
    except Exception as exc:
        return {"backend": backend, "configured": False, "status": "error", "error": str(exc)}

    if backend == "r2":
        try:
            # A missing key still proves bucket credentials work (404, not auth error).
            store.exists("__mnemosyne_healthz_probe__")
            return {
                "backend": "r2",
                "configured": True,
                "status": "ok",
                "bucket": config.R2_BUCKET or "",
                "public_base": bool(config.R2_PUBLIC_BASE_URL),
            }
        except Exception as exc:
            return {
                "backend": "r2",
                "configured": False,
                "status": "error",
                "error": str(exc),
            }

    root = config.UPLOAD_DIR
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".healthz_probe"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        writable = os.access(root, os.W_OK)
    except OSError as exc:
        return {
            "backend": "local",
            "configured": False,
            "status": "error",
            "error": str(exc),
        }
    return {
        "backend": "local",
        "configured": True,
        "status": "ok" if writable else "degraded",
        "path": str(root),
        "writable": writable,
    }


def health_summary() -> dict:
    """Aggregate runtime + storage for /healthz."""
    backends = backend_status()
    store = storage_status()
    overall = "ok"
    if store.get("status") == "error":
        overall = "error"
    elif store.get("status") == "degraded":
        overall = "degraded"
    if backends["vision"] == "grok" and not config.XAI_API_KEY:
        overall = "error" if overall == "ok" else overall
    if backends["arrange"] == "grok" and not config.XAI_API_KEY:
        overall = "error" if overall == "ok" else overall
    return {"status": overall, "backends": backends, "storage": store}