#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
DEST="$UNIT_DIR/mnemosyne.service"

echo "==> mnemosyne user service from $ROOT"

if [[ ! -f "$ROOT/.venv/bin/python" ]]; then
  echo "Missing venv — run:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[dev,r2]'"
  exit 1
fi

if [[ ! -f "$ROOT/.env" ]]; then
  if [[ -f "$ROOT/.env.example" ]]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
  fi
  echo "==> Created $ROOT/.env — edit secrets before production"
fi

mkdir -p "$UNIT_DIR"
sed "s|%h|$HOME|g" "$ROOT/ops/mnemosyne-user.service" > "$DEST"

PORT="${MNEMOSYNE_PORT:-8000}"
pkill -f "python -m mnemosyne serve" 2>/dev/null || true
sleep 1

systemctl --user daemon-reload
systemctl --user enable --now mnemosyne.service

if loginctl show-user "$(whoami)" -p Linger 2>/dev/null | grep -q 'Linger=no'; then
  echo ""
  echo "NOTE: Linger is off — mnemosyne stops when you log out."
  echo "  Enable once (needs sudo): sudo loginctl enable-linger $(whoami)"
fi

sleep 2
systemctl --user is-active mnemosyne.service
curl -sf "http://127.0.0.1:${PORT}/healthz" | head -c 400
echo
echo "==> mnemosyne running on :${PORT}. Logs: journalctl --user -u mnemosyne -f"