#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/app"
OUT="${1:-$APP/public}"
FROZEN="${FURA_FROZEN_DIR:-$APP/frozen}"
BASE_PATH="${FURA_BASE_PATH:-/chirp}"
SITE_URL="${FURA_BASE_URL:-https://lbliii.github.io/chirp}"

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT"
uv run fura freeze "$FROZEN"
uv run fura export "$OUT" \
  --frozen "$FROZEN" \
  --base-path "$BASE_PATH" \
  --base-url "$SITE_URL"

touch "$OUT/.nojekyll"
echo "Furatena static site ready: $OUT"
