#!/usr/bin/env bash
# Interactive step-by-step debug — pauses at each step so you can inspect the page
# Usage: ./debug-interactive.sh
# Press Enter in the terminal to advance to the next step.
# WARNING: closes browser at end; Ctrl+C if you want to keep it open.

set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Interactive mode: each step pauses. Press Enter to continue."
echo ""

DEBUG_VISUAL=true \
DB_INTERACTIVE=1 \
DB_SCREENSHOTS=1 \
    uv run --directory "$PWD" python -m src.main scan \
    --dry-run \
    --limit-dates 1 \
    -v \
    --screenshots \
    --interactive
