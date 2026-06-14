#!/usr/bin/env bash
# Production scan — searches all dates in the configured window
# Usage: ./scan.sh [--dry-run] [--limit-dates N]
set -euo pipefail
cd "$(dirname "$0")"

export DISPLAY="${DISPLAY:-:0}"
export DEBUG_VISUAL=true

DRY=""
LIMIT=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY="--dry-run" ;;
        --limit-dates=*) LIMIT="$arg" ;;
        *) ;;
    esac
done

echo "==> $(uv run python -c "from src.config import load_config; c=load_config('config.toml'); print(f'{c.search_window.resolved_start()} → {c.search_window.resolved_end()}')")"
echo ""

uv run --directory "$PWD" python -m src.main scan $DRY $LIMIT -v
