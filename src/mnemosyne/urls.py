"""Public URL helpers — share links behind a reverse proxy or tunnel."""
from __future__ import annotations

from fastapi import Request

from mnemosyne import config


def public_base(request: Request | None = None) -> str:
    """Canonical site origin for links we hand to clients.

    When MNEMOSYNE_PUBLIC_URL is set (e.g. a Cloudflare tunnel hostname), share
    links use that instead of request.base_url so pasted URLs work off-box.
    """
    if config.PUBLIC_URL:
        return config.PUBLIC_URL.rstrip("/")
    if request is not None:
        return str(request.base_url).rstrip("/")
    return "http://localhost:8000"


def share_url(request: Request | None, token: str) -> str:
    return f"{public_base(request)}/share/{token}"