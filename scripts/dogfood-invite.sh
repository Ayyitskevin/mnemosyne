#!/usr/bin/env bash
# Print a dogfood invite kit for an external photographer (tailnet access).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PORT="${MNEMOSYNE_PORT:-8000}"
PUBLIC_URL="${MNEMOSYNE_PUBLIC_URL:-}"
TAILNET_DNS=""

if command -v tailscale >/dev/null 2>&1; then
  TAILNET_DNS=$(tailscale status --json | python3 -c "
import json, sys
print(json.load(sys.stdin).get('Self', {}).get('DNSName', '').rstrip('.'))
" 2>/dev/null || true)
fi

if [[ -z "$PUBLIC_URL" && -n "$TAILNET_DNS" ]]; then
  PUBLIC_URL="http://${TAILNET_DNS}:${PORT}"
fi

if [[ -z "$PUBLIC_URL" ]]; then
  PUBLIC_URL="http://127.0.0.1:${PORT}"
fi

BASE="${PUBLIC_URL%/}"
HTTPS_OK=0
if [[ "$BASE" == https://* ]]; then
  HTTPS_OK=1
fi

cat <<EOF
=== mnemosyne dogfood invite ===

Share with your photographer:

  App:     ${BASE}/
  Sign up: ${BASE}/signup
  Log in:  ${BASE}/login

Before they start:
  1. Invite them to your Tailscale tailnet (Admin → Users → Invite).
     They install Tailscale and accept — then the URL above works on their machine.
  2. Ask them to use a real culled gallery (JPEGs, one album).
  3. Pick a gallery theme on upload (food / wedding / general / event).
  4. Note: billing is off — no payment step during dogfood.
  5. Optional print cross-sell: bash scripts/dogfood-plutus-prod.sh (after suite loop).

What to watch:
  - Upload friction (size, wait time)
  - First-draft quality (story order, hero picks)
  - Edit + PDF export
  - Share link if they want client review

EOF

if [[ "$HTTPS_OK" == "0" ]]; then
  cat <<'EOF'
HTTPS (recommended for share links):
  sudo tailscale set --operator=$USER
  bash scripts/wire-public-url.sh --tailscale

EOF
fi

if curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
  echo "Local health: OK (http://127.0.0.1:${PORT}/healthz)"
else
  echo "WARN: mnemosyne not listening on :${PORT} — start or restart the service first."
fi

echo ""
echo "Waitlist signups: sqlite3 on MNEMOSYNE_DB → SELECT email, created_at FROM waitlist;"