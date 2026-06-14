#!/usr/bin/env bash
# Run tests
# Usage: ./test.sh

set -euo pipefail
cd "$(dirname "$0")"

uv run --directory "$PWD" pytest tests/ -v "$@"
