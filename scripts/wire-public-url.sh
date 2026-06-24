#!/usr/bin/env bash
# Set MNEMOSYNE_PUBLIC_URL for pasteable share links behind a tunnel or reverse proxy.
# Optional: expose the app on the Tailscale tailnet via `tailscale serve`.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${MNEMOSYNE_ENV_FILE:-$ROOT/.env}"
PORT="${MNEMOSYNE_PORT:-8000}"
PUBLIC_URL="${MNEMOSYNE_PUBLIC_URL:-}"
TAILSCALE_SERVE="${MNEMOSYNE_TAILSCALE_SERVE:-}"
SYSTEMD_UNIT="${MNEMOSYNE_SYSTEMD_UNIT:-mnemosyne.service}"

if [[ "${1:-}" == --tailscale ]]; then
  TAILSCALE_SERVE=1
  PUBLIC_URL=""
elif [[ -z "$PUBLIC_URL" && -n "${1:-}" ]]; then
  PUBLIC_URL="$1"
fi

if [[ -z "$PUBLIC_URL" && "$TAILSCALE_SERVE" == "1" ]]; then
  if ! command -v tailscale >/dev/null 2>&1; then
    echo "tailscale not installed — set MNEMOSYNE_PUBLIC_URL manually" >&2
    exit 1
  fi
  DNS_NAME=$(tailscale status --json | python3 -c "
import json, sys
self_host = json.load(sys.stdin).get('Self', {}).get('DNSName', '')
print(self_host.rstrip('.'))
")
  if [[ -z "$DNS_NAME" ]]; then
    echo "could not read tailscale DNS name" >&2
    exit 1
  fi
  echo "==> Tailscale serve https://$DNS_NAME → 127.0.0.1:${PORT}"
  if ! tailscale serve --bg --https=443 "http://127.0.0.1:${PORT}" 2>&1; then
    echo "tailscale serve failed — run once: sudo tailscale set --operator=\$USER" >&2
    echo "then: bash scripts/wire-public-url.sh --tailscale" >&2
    exit 1
  fi
  PUBLIC_URL="https://${DNS_NAME}"
fi

if [[ -z "$PUBLIC_URL" ]]; then
  echo "Usage: MNEMOSYNE_PUBLIC_URL=https://mnemosyne.example.com bash scripts/wire-public-url.sh" >&2
  echo "   or: bash scripts/wire-public-url.sh https://mnemosyne.example.com" >&2
  echo "   or: bash scripts/wire-public-url.sh --tailscale" >&2
  exit 1
fi

PUBLIC_URL="${PUBLIC_URL%/}"

echo "==> Wire public URL → $PUBLIC_URL"
python3 - <<PY
from pathlib import Path

env_path = Path("${ENV_FILE}")
updates = {"MNEMOSYNE_PUBLIC_URL": "${PUBLIC_URL}"}
lines = env_path.read_text().splitlines() if env_path.exists() else []
out, seen = [], set()
for line in lines:
    if "=" in line and not line.strip().startswith("#"):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
            continue
    out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
env_path.write_text("\\n".join(out).rstrip() + "\\n")
print("wrote", env_path)
PY

if systemctl --user is-active "$SYSTEMD_UNIT" >/dev/null 2>&1; then
  echo "==> Restart $SYSTEMD_UNIT"
  systemctl --user restart "$SYSTEMD_UNIT"
  sleep 2
fi

echo "==> Verify healthz"
if curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
  curl -sf "http://127.0.0.1:${PORT}/healthz" | python3 -c "
import json, sys
h = json.load(sys.stdin)
print('  ok:', h.get('ok'))
"
else
  echo "  (mnemosyne not listening on :${PORT} — restart your process to pick up .env)"
fi

echo "Done — share links will use ${PUBLIC_URL}"