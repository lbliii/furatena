#!/usr/bin/env sh
set -eu

# Import the server stack before checking. A non-free-threading-safe extension
# can enable the GIL when imported, which would make a bare interpreter check a
# false positive.
python -c 'import sys; from furatena.catalog.docs_app import DocsApp; assert not sys._is_gil_enabled(), "Furatena requires a GIL-disabled runtime"'

APP_ROOT=/app/app
if [ -n "${FURA_CONTENT_REPOSITORY:-}" ]; then
  fura content reconcile
  CONTENT_STATE_ROOT="${FURA_CONTENT_STATE_ROOT:-/data/furatena}"
  APP_ROOT="$CONTENT_STATE_ROOT/active/source/${FURA_CONTENT_SUBDIRECTORY:-app}"
  FURA_FROZEN_DIR="$CONTENT_STATE_ROOT/active/frozen"
  export FURA_FROZEN_DIR
fi

exec fura --app-root "$APP_ROOT" serve \
  --preview \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1
