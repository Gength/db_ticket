#!/usr/bin/env bash
# Capture the DB API requests by opening a browser and letting you manually search.
# All API calls (POST/XHR/fetch) are logged to /tmp/db_api_capture.json
#
# Usage: ./capture-api.sh
#   1. Browser opens at bahn.de
#   2. Fill the form manually (Berlin → München, date, etc.)
#   3. Click Search
#   4. When results load, press Enter in this terminal
#   5. Check /tmp/db_api_capture.json for the captured API calls
set -euo pipefail
cd "$(dirname "$0")/.."

DEBUG_VISUAL=true uv run --directory "$PWD" python src/capture_api.py
