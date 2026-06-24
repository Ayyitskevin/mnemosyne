#!/usr/bin/env bash
# Wire mnemosyne vision delegation to Argus (homelab or SaaS upload mode).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${MNEMOSYNE_ENV_FILE:-$ROOT/.env}"

ARGUS_URL="${MNEMOSYNE_ARGUS_URL:-${ARGUS_URL:-http://127.0.0.1:8010}}"
ARGUS_TOKEN="${MNEMOSYNE_ARGUS_API_TOKEN:-${ARGUS_API_TOKEN:-}}"
DELEGATION_MODE="${MNEMOSYNE_ARGUS_DELEGATION_MODE:-path}"
VISION_BACKEND="${MNEMOSYNE_VISION_BACKEND:-argus}"

upsert() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >>"$ENV_FILE"
  fi
}

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT/.env.example" "$ENV_FILE"
  echo "Created $ENV_FILE from .env.example"
fi

upsert MNEMOSYNE_ARGUS_URL "$ARGUS_URL"
upsert MNEMOSYNE_ARGUS_DELEGATION_MODE "$DELEGATION_MODE"
upsert MNEMOSYNE_VISION_BACKEND "$VISION_BACKEND"
if [[ -n "$ARGUS_TOKEN" ]]; then
  upsert MNEMOSYNE_ARGUS_API_TOKEN "$ARGUS_TOKEN"
fi

echo "==> Argus vision wired in $ENV_FILE"
echo "    MNEMOSYNE_ARGUS_URL=$ARGUS_URL"
echo "    MNEMOSYNE_ARGUS_DELEGATION_MODE=$DELEGATION_MODE"
echo "    MNEMOSYNE_VISION_BACKEND=$VISION_BACKEND"
echo "    token: ${ARGUS_TOKEN:+set}${ARGUS_TOKEN:-unset}"
echo ""
echo "Next: bash scripts/dogfood-argus-vision.sh"