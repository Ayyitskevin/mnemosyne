"""Application configuration, read from the environment.

Keeping config in one place (and env-driven) means the same code runs here on
mickey and anywhere else without edits — you just point the env vars elsewhere.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env (gitignored, per-machine) before reading any env var below, so secrets
# like XAI_API_KEY live in a file off the repo rather than the shell history.
load_dotenv()

# The SQLite file mnemosyne stores everything in. Override with MNEMOSYNE_DB.
DB_PATH = Path(os.environ.get("MNEMOSYNE_DB", "mnemosyne.db"))

# Signing key for the session cookie (Starlette SessionMiddleware). A logged-in
# user's id rides in a cookie signed with this; rotating it logs everyone out.
# In prod set MNEMOSYNE_SECRET_KEY to a fixed random value (in .env, off the repo)
# so restarts don't drop sessions — the dev fallback is a fresh key per boot.
SECRET_KEY = os.environ.get("MNEMOSYNE_SECRET_KEY") or os.urandom(32).hex()

# Where web-uploaded photos are written, one subfolder per created album. Ingest
# records absolute paths into these files, and /photo/<id> later serves them off
# disk, so this is permanent storage, not scratch — gitignored, per-machine.
UPLOAD_DIR = Path(os.environ.get("MNEMOSYNE_UPLOAD_DIR", "uploads"))

# Which storage driver backs photo bytes (see storage.py). "local" = this box's
# filesystem (the only driver today). An object-store driver ("r2"/"s3") drops in
# here later without touching ingest/vision/export/web — that swappability is the
# whole point of the storage seam.
STORAGE_BACKEND = os.environ.get("MNEMOSYNE_STORAGE_BACKEND", "local")

# Cap on photos per web upload. The vision pipeline now runs in the background
# worker, not in the request, so this is no longer about request timeouts — it's
# a sanity bound on one submit (disk + a single album's queue time), sized for a
# real wedding/event gallery. The CLI `build` path has no cap (local operator,
# not a web stranger).
MAX_ALBUM_UPLOAD = int(os.environ.get("MNEMOSYNE_MAX_ALBUM_UPLOAD", "500"))

# Per-file and pixel-count upload caps. A file may have a safe-looking suffix but
# still be a decompression bomb or a huge payload; validate before anything is
# committed into an album job.
MAX_UPLOAD_FILE_BYTES = int(
    os.environ.get("MNEMOSYNE_MAX_UPLOAD_FILE_BYTES", str(50 * 1024 * 1024))
)
MAX_UPLOAD_PIXELS = int(
    os.environ.get("MNEMOSYNE_MAX_UPLOAD_PIXELS", str(100_000_000))
)

# How long (seconds) a worker's claim on a 'processing' album is trusted before
# another worker may reclaim it. It guards crash recovery in a multi-process
# setup: a job whose lease has gone stale is assumed to belong to a dead worker
# and is re-queued. There is no heartbeat, so this must comfortably exceed the
# longest real build — a job that outruns the lease can be re-run by a sibling,
# which is wasteful but safe because process_album is idempotent.
WORKER_LEASE_SECONDS = int(os.environ.get("MNEMOSYNE_WORKER_LEASE_SECONDS", "900"))

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
ARGUS_API_TOKEN = os.environ.get("ARGUS_API_TOKEN") or os.environ.get("MNEMOSYNE_ARGUS_API_TOKEN")
# path = argus reads image_path on its host (homelab shared disk).
# upload = multipart file POST (decoupled hosts / Argus SaaS upload-only).
ARGUS_DELEGATION_MODE = os.environ.get("MNEMOSYNE_ARGUS_DELEGATION_MODE", "path").strip().lower()

# Phase 2 runtime vision vendor: xAI / Grok (OpenAI-compatible API). This is the
# SaaS production lane — a metered API bill, separate from any coding subscription.
# Local qwen3-vl stays the dev A/B oracle + dogfood path, NOT prod failover.
XAI_API_KEY = os.environ.get("XAI_API_KEY")
XAI_BASE_URL = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
# grok-2-vision was retired May 2026; the current lineup (grok-4.3 / grok-4.20) is
# all multimodal. For a per-photo scene+hero classify (judgment, not reasoning —
# CLAUDE.md Rule 5) the non-reasoning 4.20 variant is the cheapest right fit. The
# id was verified to resolve against the live API 2026-06-23 (403-no-credits, not
# 404), so it's real; a billed call still needs credits loaded on the xAI team.
GROK_VISION_MODEL = os.environ.get("MNEMOSYNE_GROK_VISION_MODEL", "grok-4.20-non-reasoning")

# Explicit per-photo vision backend selector: "grok" | "argus" | "ollama".
# Unset = local-first default (argus if ARGUS_URL set, else ollama) — keeps the
# existing dogfood path unsurprised. Flipping this one var is the whole A/B.
VISION_BACKEND = os.environ.get("MNEMOSYNE_VISION_BACKEND")

# Where per-photo cloud-vision cost lines (tokens + latency) are appended, so
# $/album is reconstructable from call one. Local + gitignored by default; high
# volume, so deliberately NOT the shared fleet routing log.
ROUTING_LOG = Path(os.environ.get("MNEMOSYNE_ROUTING_LOG", "mnemosyne-vision.log"))
