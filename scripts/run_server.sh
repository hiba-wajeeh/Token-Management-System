#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d ".venv" ]]; then
  echo "❌ .venv not found. Run: ./scripts/bootstrap_mac.sh"
  exit 1
fi

# shellcheck disable=SC1091
source "$ROOT_DIR/.venv/bin/activate"

cd "$ROOT_DIR/server"
python server5.py
