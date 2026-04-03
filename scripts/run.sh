#!/usr/bin/env bash
# Set up environment and run the app locally.
# Usage: ./scripts/run.sh [--skip-setup]

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

SKIP_SETUP=false
if [ "${1:-}" = "--skip-setup" ]; then
    SKIP_SETUP=true
fi

# Load local environment config
if [ -f "config/.env.local" ]; then
    set -a
    source config/.env.local
    set +a
else
    echo "Warning: config/.env.local not found, using defaults"
fi

PORT="${BACKEND_PORT:-8000}"

# --- Setup ---
if [ "$SKIP_SETUP" = false ]; then
    echo "=== Setting up environment ==="

    # Python virtual environment
    if [ ! -d ".venv" ]; then
        echo "Creating Python virtual environment..."
        python3 -m venv .venv
    fi
    source .venv/bin/activate

    echo "Upgrading pip and setuptools..."
    pip install --upgrade pip setuptools --quiet

    echo "Installing Python dependencies..."
    pip install -e ".[dev,dashboard]" --quiet

    echo "Installing Playwright browsers..."
    playwright install chromium --with-deps 2>/dev/null || playwright install chromium

    # Frontend dependencies
    echo "Installing frontend dependencies..."
    cd src/frontend
    npm install --silent
    cd "$REPO_ROOT"

    echo "=== Setup complete ==="
else
    source .venv/bin/activate
    echo "=== Skipping setup (--skip-setup) ==="
fi

# --- Run ---
echo ""
echo "Starting Smart Shopping Agent..."
echo "  Backend:  http://localhost:$PORT"
echo "  Frontend: http://localhost:3000"

# Start Docker browser container if BROWSER_WS_ENDPOINT is set and Docker is available
DOCKER_STARTED=false
if [ -n "${BROWSER_WS_ENDPOINT:-}" ] && command -v docker >/dev/null 2>&1; then
    if ! curl -sf http://localhost:3001 >/dev/null 2>&1; then
        echo "  Browser:   ws://localhost:3001 (Docker)"
        docker compose up -d --build 2>&1 | tail -3
        DOCKER_STARTED=true
        # Wait for browser to be ready
        for i in $(seq 1 30); do
            curl -sf http://localhost:3001 >/dev/null 2>&1 && break
            sleep 1
        done
    else
        echo "  Browser:   ws://localhost:3001 (already running)"
    fi
elif [ -n "${BROWSER_WS_ENDPOINT:-}" ]; then
    echo "  Warning: BROWSER_WS_ENDPOINT set but Docker not found — install Docker Desktop"
fi

# Start backend in background
uvicorn src.backend.main:app --reload --host "${BACKEND_HOST:-0.0.0.0}" --port "$PORT" &
BACKEND_PID=$!

# Start frontend in background
cd src/frontend
npm run dev &
FRONTEND_PID=$!
cd "$REPO_ROOT"

# Start Ollama if an ollama model is configured
OLLAMA_PID=""
if echo "${LLM_MODEL:-}${SCRAPER_LLM_MODEL:-}" | grep -q "ollama/"; then
    OLLAMA_BIN="$(command -v ollama 2>/dev/null || echo "")"
    # Fallback to app bundle location
    [ -z "$OLLAMA_BIN" ] && [ -x "/Applications/Ollama.app/Contents/Resources/ollama" ] && \
        OLLAMA_BIN="/Applications/Ollama.app/Contents/Resources/ollama"
    if [ -n "$OLLAMA_BIN" ]; then
        if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
            echo "  Ollama:    http://localhost:11434"
            "$OLLAMA_BIN" serve &
            OLLAMA_PID=$!
            # Wait for Ollama to be ready
            for i in $(seq 1 10); do
                curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && break
                sleep 1
            done
        else
            echo "  Ollama:    http://localhost:11434 (already running)"
        fi
    else
        echo "  Warning: ollama/ model configured but ollama binary not found"
    fi
fi

# Start Phoenix dashboard if installed
PHOENIX_PID=""
PHOENIX_PORT="${PHOENIX_PORT:-6006}"
if python -c "import phoenix" 2>/dev/null; then
    echo "  Dashboard: http://localhost:$PHOENIX_PORT"
    python -m src.dashboard.server --port "$PHOENIX_PORT" &
    PHOENIX_PID=$!
else
    echo "  Dashboard: not installed (pip install -e '.[dashboard]' to enable)"
fi

echo ""

# Trap to clean up all processes on exit
cleanup() {
    echo ""
    echo "Shutting down..."
    kill "$BACKEND_PID" 2>/dev/null || true
    kill "$FRONTEND_PID" 2>/dev/null || true
    [ -n "$PHOENIX_PID" ] && kill "$PHOENIX_PID" 2>/dev/null || true
    [ -n "$OLLAMA_PID" ] && kill "$OLLAMA_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
    [ -n "$PHOENIX_PID" ] && wait "$PHOENIX_PID" 2>/dev/null || true
    [ -n "$OLLAMA_PID" ] && wait "$OLLAMA_PID" 2>/dev/null || true
    if [ "$DOCKER_STARTED" = true ]; then
        docker compose down 2>/dev/null || true
    fi
    echo "Done."
}
trap cleanup EXIT INT TERM

# Wait for any process to exit
wait
