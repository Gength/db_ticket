#!/usr/bin/env bash
# Headed debug — opens visible browser, searches 1 date
# Usage: ./debug.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# --- Pre-flight checks ---
echo "==> Checking environment..."

# Detect display
if [[ -z "${DISPLAY:-}" ]]; then
    # Try common WSLg values
    if [[ -S /tmp/.X11-unix/X0 ]]; then
        export DISPLAY=:0
    elif [[ -S /tmp/.X11-unix/X1 ]]; then
        export DISPLAY=:1
    else
        echo "ERROR: No X11 socket found. Is WSLg running?"
        echo "  Try: export DISPLAY=\$(cat /etc/resolv.conf | grep nameserver | awk '{print \$2}'):0"
        exit 1
    fi
fi

echo "  DISPLAY=$DISPLAY"
echo "  X11 socket: $(ls /tmp/.X11-unix/ 2>/dev/null || echo 'NONE')"

# Check Chrome
if ! command -v google-chrome &>/dev/null; then
    echo "ERROR: google-chrome not found"
    exit 1
fi
echo "  Chrome: $(google-chrome --version)"

# Quick X11 test
if command -v xdpyinfo &>/dev/null; then
    xdpyinfo -display "$DISPLAY" &>/dev/null && echo "  X11: OK" || echo "  X11: FAILED (display $DISPLAY not reachable)"
fi

export DEBUG_VISUAL=true

echo ""
echo "==> Launching scanner..."
uv run --directory "$PWD" python -m src.main scan --dry-run --limit-dates 1 -v
