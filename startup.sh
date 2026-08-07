#!/usr/bin/env bash
# ===================================================================
#  Dynamic Pricing Engine - one-click startup (macOS / Linux)
#  FR-074, FR-075, FR-076
#
#  User-space only: no Docker, no sudo, no system services.
#  Every process started here is a child of this shell and is cleaned
#  up on exit (NFR-023).
# ===================================================================

set -euo pipefail
cd "$(dirname "$0")"

GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
info()  { printf "  [ .. ] %s\n" "$1"; }
ok()    { printf "  [${GREEN} OK ${RESET}] %s\n" "$1"; }
warn()  { printf "  [${YELLOW}WARN${RESET}] %s\n" "$1"; }
fail()  { printf "  [${RED}FAIL${RESET}] %s\n" "$1"; exit 1; }

PIDS=()
cleanup() {
    printf "\n  Shutting down...\n"
    for pid in "${PIDS[@]:-}"; do
        if [[ -n "${pid}" ]] && kill -0 "$pid" 2>/dev/null; then
            # SIGTERM so lifespan handlers flush telemetry and close SQLite.
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    wait 2>/dev/null || true
    printf "  Stopped.\n"
}
trap cleanup EXIT INT TERM

printf "\n  Dynamic Pricing Engine\n"
printf "  ----------------------------------------------------------------\n"

# --- Python -----------------------------------------------------------
PYBIN="$(command -v python3 || command -v python || true)"
[[ -n "$PYBIN" ]] || fail "Python not found on PATH. Install Python 3.12."

# --- Virtual environment ----------------------------------------------
if [[ ! -x ".venv/bin/python" ]]; then
    info "Creating virtual environment..."
    "$PYBIN" -m venv .venv || fail "Could not create .venv"
fi
PY="$PWD/.venv/bin/python"

# --- Dependencies -------------------------------------------------------
if ! "$PY" -c "import fastapi, numpy, scipy" >/dev/null 2>&1; then
    info "Installing dependencies (first run, this takes a few minutes)..."
    "$PY" -m pip install --upgrade pip --quiet
    "$PY" -m pip install -r requirements.txt --quiet || fail "Dependency install failed."
fi

# --- Environment file ---------------------------------------------------
if [[ ! -f ".env" ]]; then
    info "Creating .env from .env.example"
    cp .env.example .env
    warn "Review .env before relying on LLM features."
fi

export PYTHONPATH="$PWD"

# --- Seed synthetic data -------------------------------------------------
if [[ ! -f "data/commerce.db" ]]; then
    info "Seeding synthetic retail dataset..."
    "$PY" -m commerce.seed || fail "Seeding failed."
fi

# --- Reclaim our ports ----------------------------------------------------
# A leftover service from a previous run answers /health perfectly well, so
# without this the launcher adopts it, reports success, and leaves you talking
# to old code while the process it just started dies unseen on a bind error.
# Pass --force to stop the holders instead of refusing.
FORCE=""
[[ "${1:-}" == "--force" ]] && FORCE=1

port_owner() {
    if command -v lsof >/dev/null 2>&1; then
        lsof -ti "tcp:$1" -sTCP:LISTEN 2>/dev/null | head -n1
    elif command -v ss >/dev/null 2>&1; then
        ss -lptnH "sport = :$1" 2>/dev/null |
            grep -o 'pid=[0-9]*' | head -n1 | cut -d= -f2
    fi
}

info "Checking ports 8001, 8000, 5173..."
BUSY=""
for entry in "8001:Commerce Service" "8000:Pricing Platform" "5173:Web UI"; do
    port="${entry%%:*}"; label="${entry#*:}"
    owner="$(port_owner "$port" || true)"
    if [[ -n "$owner" ]]; then
        name="$(ps -p "$owner" -o comm= 2>/dev/null || echo unknown)"
        printf "  [%bBUSY%b] port %s (%s) held by PID %s (%s)\n" \
               "$YELLOW" "$RESET" "$port" "$label" "$owner" "$name"
        if [[ -n "$FORCE" ]]; then
            kill -TERM "$owner" 2>/dev/null || true
            sleep 1
            kill -0 "$owner" 2>/dev/null && kill -KILL "$owner" 2>/dev/null || true
        else
            BUSY=1
        fi
    else
        ok "port $port ($label) free"
    fi
done

if [[ -n "$BUSY" ]]; then
    printf "\n"
    fail "A port this stack needs is already in use — usually a previous run.
         Re-run as:  ./startup.sh --force
         to stop those processes and start clean."
fi

# --- Pre-flight ----------------------------------------------------------
info "Running pre-flight checks..."
"$PY" -m pricing.scripts.preflight || fail "Pre-flight reported a blocking failure."

# --- Commerce Service (8001) ----------------------------------------------
info "Starting Commerce Service on port 8001..."
"$PY" -m uvicorn commerce.main:app --host 127.0.0.1 --port 8001 &
PIDS+=($!)

# Commerce must be healthy before the platform accepts runs (FR-076).
info "Waiting for Commerce Service to become healthy..."
for i in $(seq 1 20); do
    if "$PY" -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8001/health',timeout=2).json().get('status')=='ok' else 1)" >/dev/null 2>&1; then
        ok "Commerce Service healthy."
        break
    fi
    [[ $i -eq 20 ]] && fail "Commerce Service did not become healthy within ~20s."
    sleep 1
done

# --- Pricing Platform (8000) -----------------------------------------------
info "Starting Pricing Platform on port 8000..."
"$PY" -m uvicorn pricing.main:app --host 127.0.0.1 --port 8000 &
PIDS+=($!)

# --- UI (5173) --------------------------------------------------------------
if [[ -f "ui/package.json" ]]; then
    if command -v node >/dev/null 2>&1; then
        NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
        if (( NODE_MAJOR < 20 )); then
            warn "Node $(node -v) detected; 20.19+ required. Skipping UI."
        else
            if [[ ! -d "ui/node_modules" ]]; then
                info "Installing UI dependencies (first run)..."
                (cd ui && npm install --silent)
            fi
            info "Starting UI on port 5173..."
            (cd ui && npm run dev) &
            PIDS+=($!)
        fi
    else
        warn "Node.js not found - skipping UI. Backend APIs still available."
    fi
else
    warn "ui/package.json not found - skipping UI."
fi

printf "\n  ----------------------------------------------------------------\n"
printf "   Commerce Service  http://127.0.0.1:8001/docs\n"
printf "   Pricing Platform  http://127.0.0.1:8000/docs\n"
printf "   Web UI            http://127.0.0.1:5173\n"
printf "  ----------------------------------------------------------------\n"
printf "   Press Ctrl+C to shut down cleanly.\n\n"

wait
