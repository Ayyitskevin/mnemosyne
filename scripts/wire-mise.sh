#!/usr/bin/env bash
# Wire mnemosyne Mise gallery import (GET /api/galleries bearer).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${MNEMOSYNE_ENV_FILE:-$ROOT/.env}"

MISE_URL="${MNEMOSYNE_MISE_URL:-${ARGUS_MISE_URL:-http://flow:8400}}"
MISE_TOKEN="${MNEMOSYNE_MISE_API_TOKEN:-${ARGUS_MISE_API_TOKEN:-${MISE_ARGUS_TOKEN:-}}}"
MISE_MEDIA_ROOT="${MNEMOSYNE_MISE_MEDIA_ROOT:-${ARGUS_MISE_MEDIA_ROOT:-}}"

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

upsert MNEMOSYNE_MISE_URL "$MISE_URL"
if [[ -n "$MISE_TOKEN" ]]; then
  upsert MNEMOSYNE_MISE_API_TOKEN "$MISE_TOKEN"
fi
if [[ -n "$MISE_MEDIA_ROOT" ]]; then
  upsert MNEMOSYNE_MISE_MEDIA_ROOT "$MISE_MEDIA_ROOT"
fi

echo "==> Mise import wired in $ENV_FILE"
echo "    MNEMOSYNE_MISE_URL=$MISE_URL"
echo "    token: ${MISE_TOKEN:+set}${MISE_TOKEN:-unset}"
echo "    MNEMOSYNE_MISE_MEDIA_ROOT=${MISE_MEDIA_ROOT:-(unset — uses API originals_path)}"
echo ""
echo "Sync media (optional): cd ~/ai-workspace/plutus && bash scripts/sync-mise-media.sh <gallery_id>"