#!/usr/bin/env bash
# Fail fast on placeholder secrets before a prod deploy.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${1:-$ROOT/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "validate-env: missing $ENV_FILE (copy from .env.example)" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

errors=0

check_not_placeholder() {
  local name="$1"
  local value="${!name:-}"
  if [[ -z "$value" ]]; then
    echo "ERROR: $name is unset"
    errors=$((errors + 1))
    return
  fi
  if [[ "$value" == *CHANGE_ME* ]]; then
    echo "ERROR: $name still contains CHANGE_ME"
    errors=$((errors + 1))
  fi
}

check_not_placeholder MNEMOSYNE_SECRET_KEY

if [[ "${MNEMOSYNE_STORAGE_BACKEND:-local}" == "r2" ]]; then
  check_not_placeholder MNEMOSYNE_R2_ENDPOINT
  check_not_placeholder MNEMOSYNE_R2_BUCKET
  check_not_placeholder MNEMOSYNE_R2_ACCESS_KEY_ID
  check_not_placeholder MNEMOSYNE_R2_SECRET_ACCESS_KEY
fi

if [[ "${MNEMOSYNE_VISION_BACKEND:-}" == "grok" || "${MNEMOSYNE_ARRANGE_BACKEND:-}" == "grok" ]]; then
  check_not_placeholder XAI_API_KEY
fi

if [[ -n "${MNEMOSYNE_PUBLIC_URL:-}" ]]; then
  if [[ "$MNEMOSYNE_PUBLIC_URL" != https://* ]]; then
    echo "WARN: MNEMOSYNE_PUBLIC_URL should be https:// for client share links"
  fi
else
  echo "WARN: MNEMOSYNE_PUBLIC_URL unset — share links will use request host only"
fi

if [[ "$errors" -gt 0 ]]; then
  echo "validate-env: $errors error(s)" >&2
  exit 1
fi
echo "validate-env: OK ($ENV_FILE)"