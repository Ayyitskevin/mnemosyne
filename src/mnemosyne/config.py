"""Application configuration, read from the environment.

Keeping config in one place (and env-driven) means the same code runs here on
mickey and anywhere else without edits — you just point the env vars elsewhere.
"""
import os
from pathlib import Path

# The SQLite file mnemosyne stores everything in. Override with MNEMOSYNE_DB.
DB_PATH = Path(os.environ.get("MNEMOSYNE_DB", "mnemosyne.db"))

# The local Ollama fleet on mickey. Phase 0 runs ENTIRELY on-box — images are
# analyzed here and never leave the machine, so "we don't train on your images"
# is true by construction. (When mnemosyne becomes a hosted SaaS, these point at
# cloud inference and that cost gets priced into the subscription.)
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# qwen3-vl:32b is mickey's clean/automation vision lane; qwen3.6:35b is a mid
# reasoning model for the arrange step. Both overridable via env.
VISION_MODEL = os.environ.get("MNEMOSYNE_VISION_MODEL", "qwen3-vl:32b")
ARRANGE_MODEL = os.environ.get("MNEMOSYNE_ARRANGE_MODEL", "qwen3.6:35b")

# Phase 3: optional delegation of vision "look" step to argus service (over tailnet or local).
# When set (e.g. "http://mickey:8010" or "http://127.0.0.1:8010"), mnemosyne vision will
# call argus /analyze (path must be visible to argus server) and map results to {scene, hero_score}.
# This keeps mnemosyne from loading heavy vision models directly during integration dev.
# Force argus side with ARGUS_VISION_BACKEND=mock.
ARGUS_URL = os.environ.get("ARGUS_URL") or os.environ.get("MNEMOSYNE_ARGUS_URL")
