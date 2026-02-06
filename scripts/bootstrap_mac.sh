#!/usr/bin/env bash
set -euo pipefail

echo "✅ PAD QMS Mac bootstrap starting..."

# ---------- helpers ----------
need_cmd() { command -v "$1" >/dev/null 2>&1; }
info() { echo -e "\n👉 $1"; }
ok()   { echo -e "✅ $1"; }
warn() { echo -e "⚠️  $1"; }

# ---------- repo root ----------
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$ROOT_DIR/server"

if [[ ! -d "$SERVER_DIR" ]]; then
  echo "❌ server/ folder not found at: $SERVER_DIR"
  exit 1
fi

# ---------- Homebrew ----------
info "Checking Homebrew..."
if ! need_cmd brew; then
  echo "❌ Homebrew not found. Install from https://brew.sh then re-run."
  exit 1
fi
ok "Homebrew OK"

# ---------- Install deps ----------
info "Installing PostgreSQL 18 via Homebrew (and dependencies)..."
brew update >/dev/null
brew install postgresql@18 >/dev/null || true
brew install icu4c@78 >/dev/null || true

# pg18 is keg-only; determine its bin path
BREW_PG18_PREFIX="$(brew --prefix postgresql@18)"
PG_BIN="$BREW_PG18_PREFIX/bin"
PSQL="$PG_BIN/psql"
CREATEUSER="$PG_BIN/createuser"

if [[ ! -x "$PSQL" ]]; then
  echo "❌ psql not found at: $PSQL"
  echo "Check: brew info postgresql@18"
  exit 1
fi

ok "PostgreSQL@18 installed at: $BREW_PG18_PREFIX"

# ---------- Add PATH to zshrc (optional but helpful) ----------
info "Ensuring PostgreSQL@18 is on PATH for your shell..."
ZSHRC="$HOME/.zshrc"
EXPORT_LINE="export PATH=\"$BREW_PG18_PREFIX/bin:\$PATH\""
if [[ -f "$ZSHRC" ]] && ! grep -qF "$EXPORT_LINE" "$ZSHRC"; then
  echo "$EXPORT_LINE" >> "$ZSHRC"
  ok "Added pg18 PATH line to ~/.zshrc"
else
  ok "PATH line already present (or ~/.zshrc missing)"
fi

# ---------- Port check ----------
info "Checking if port 5432 is free..."
if sudo lsof -nP -iTCP:5432 -sTCP:LISTEN >/dev/null 2>&1; then
  warn "Port 5432 is in use. Attempting to stop common Postgres services..."
  brew services stop postgresql@14 >/dev/null 2>&1 || true
  brew services stop postgresql >/dev/null 2>&1 || true
  brew services stop postgresql@18 >/dev/null 2>&1 || true

  # Try to kill leftover postgres processes owned by current user (safe for dev boxes)
  pkill -u "$(id -u)" -f "postgres.*-D" >/dev/null 2>&1 || true

  # Re-check
  if sudo lsof -nP -iTCP:5432 -sTCP:LISTEN >/dev/null 2>&1; then
    warn "Port 5432 still in use. You can either:"
    warn "  A) stop the other service using 5432, OR"
    warn "  B) edit /opt/homebrew/var/postgresql@18/postgresql.conf and set port=5433"
    echo
    sudo lsof -nP -iTCP:5432 -sTCP:LISTEN || true
    exit 1
  fi
fi
ok "Port 5432 is free"

# ---------- Start PostgreSQL@18 ----------
info "Starting PostgreSQL@18 as a background service..."
brew services restart postgresql@18 >/dev/null || true

# Service log location (from launchctl info)
LOG_FILE="/opt/homebrew/var/log/postgresql@18.log"
if [[ -f "$LOG_FILE" ]]; then
  ok "Postgres log: $LOG_FILE"
fi

# Wait for it to accept connections
info "Waiting for Postgres to accept connections..."
for i in {1..20}; do
  if "$PSQL" -h 127.0.0.1 -p 5432 -d postgres -c "SELECT 1" >/dev/null 2>&1; then
    ok "Postgres is accepting connections"
    break
  fi
  sleep 0.5
  if [[ $i -eq 20 ]]; then
    echo "❌ Postgres did not become ready. Check logs:"
    [[ -f "$LOG_FILE" ]] && tail -n 120 "$LOG_FILE" || true
    exit 1
  fi
done

# ---------- Ensure a superuser role exists for current mac user ----------
# Homebrew clusters often allow local socket auth; sometimes role doesn't exist.
USER_NAME="$(whoami)"

info "Ensuring Postgres role exists for macOS user: $USER_NAME"
if "$PSQL" -h 127.0.0.1 -p 5432 -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$USER_NAME'" | grep -q 1; then
  ok "Role '$USER_NAME' exists"
else
  # create role as superuser; if this fails, user can still use 'postgres' role manually
  "$CREATEUSER" -h 127.0.0.1 -p 5432 -s "$USER_NAME" >/dev/null 2>&1 || true
  ok "Attempted to create superuser role '$USER_NAME' (ok if already created)"
fi

# ---------- Create DB + user (idempotent) ----------
info "Creating qms_test database and qms_test_user (idempotent)..."

"$PSQL" -h 127.0.0.1 -p 5432 -d postgres -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'qms_test_user') THEN
    CREATE ROLE qms_test_user LOGIN PASSWORD 'qms@1234';
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'qms_test') THEN
    CREATE DATABASE qms_test OWNER qms_test_user;
  END IF;
END $$;
SQL

# Schema grants + default privileges in the qms_test db
"$PSQL" -h 127.0.0.1 -p 5432 -d qms_test -v ON_ERROR_STOP=1 <<'SQL'
GRANT USAGE, CREATE ON SCHEMA public TO qms_test_user;

ALTER DEFAULT PRIVILEGES FOR USER qms_test_user IN SCHEMA public
GRANT ALL ON TABLES TO qms_test_user;

ALTER DEFAULT PRIVILEGES FOR USER qms_test_user IN SCHEMA public
GRANT ALL ON SEQUENCES TO qms_test_user;
SQL

ok "Database setup complete"

# ---------- Ensure server config points to local postgres ----------
info "Updating server/config.ini to use local Postgres (127.0.0.1:5432)..."
CONFIG_INI="$SERVER_DIR/config.ini"
if [[ ! -f "$CONFIG_INI" ]]; then
  warn "server/config.ini not found. Skipping update."
else
  # best-effort replace values under [postgres]
  python3 - <<PY
import configparser, pathlib
p = pathlib.Path("$CONFIG_INI")
cfg = configparser.ConfigParser()
cfg.read(p)
if "postgres" not in cfg:
    cfg["postgres"] = {}
cfg["postgres"]["host"] = "127.0.0.1"
cfg["postgres"]["port"] = "5432"
cfg["postgres"]["db"] = "qms_test"
cfg["postgres"]["user"] = "qms_test_user"
cfg["postgres"]["password"] = "qms@1234"
with p.open("w") as f:
    cfg.write(f)
print("✅ Wrote:", p)
PY
fi

# ---------- Python venv + deps ----------
info "Setting up Python virtualenv..."
cd "$ROOT_DIR"

if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  python3 -m venv .venv
  ok "Created .venv"
else
  ok ".venv already exists"
fi

# shellcheck disable=SC1091
source "$ROOT_DIR/.venv/bin/activate"

python -m pip install --upgrade pip >/dev/null

if [[ -f "$ROOT_DIR/requirements.txt" ]]; then
  info "Installing Python requirements..."
  pip install -r "$ROOT_DIR/requirements.txt"
  ok "Python deps installed"
else
  warn "requirements.txt not found at repo root. Skipping pip install."
fi

ok "Bootstrap completed ✅"
echo
echo "Next:"
echo "  1) source .venv/bin/activate"
echo "  2) ./scripts/run_server.sh"
