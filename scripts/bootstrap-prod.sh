#!/usr/bin/env bash
# Prod bootstrap: validate env, optional R2 + public URL, install user service.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

WIRE_R2=0
WIRE_URL=0
PUBLIC_URL="${MNEMOSYNE_PUBLIC_URL:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --r2) WIRE_R2=1; shift ;;
    --tailscale) WIRE_URL=1; PUBLIC_URL=""; shift ;;
    --url)
      PUBLIC_URL="${2:?}"
      WIRE_URL=1
      shift 2
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example — edit secrets, then re-run."
  exit 1
fi

bash "$ROOT/scripts/validate-env.sh" "$ROOT/.env"

if [[ "$WIRE_R2" == "1" ]]; then
  bash "$ROOT/scripts/wire-r2.sh"
fi

if [[ "$WIRE_URL" == "1" ]]; then
  if [[ -n "$PUBLIC_URL" ]]; then
    bash "$ROOT/scripts/wire-public-url.sh" "$PUBLIC_URL"
  else
    bash "$ROOT/scripts/wire-public-url.sh" --tailscale
  fi
fi

bash "$ROOT/scripts/install-service.sh"
echo "==> Prod bootstrap complete"