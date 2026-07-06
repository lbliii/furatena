#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/app"
OUT="${1:-$APP/public}"
FROZEN="${FURA_FROZEN_DIR:-$APP/frozen}"
BASE_PATH="${FURA_BASE_PATH:-/furatena}"
SITE_URL="${FURA_BASE_URL:-https://lbliii.github.io/furatena}"

export PYTHON_GIL="${PYTHON_GIL:-0}"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT"
rm -rf "$FROZEN" "$OUT"
uv run fura freeze "$FROZEN"
uv run fura export "$OUT" \
  --frozen "$FROZEN" \
  --base-path "$BASE_PATH" \
  --base-url "$SITE_URL"

touch "$OUT/.nojekyll"
for required in index.html catalog.json sitemap.xml .nojekyll; do
  if [[ ! -f "$OUT/$required" ]]; then
    echo "missing required Pages artifact: $OUT/$required" >&2
    exit 1
  fi
done
echo "Furatena static site ready: $OUT"
