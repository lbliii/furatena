#!/usr/bin/env sh
set -eu

# Import the server stack before checking. A non-free-threading-safe extension
# can enable the GIL when imported, which would make a bare interpreter check a
# false positive.
python -c 'import sys; from furatena.catalog.docs_app import DocsApp; assert not sys._is_gil_enabled(), "Furatena requires a GIL-disabled runtime"'

exec fura --app-root /app/app serve \
  --preview \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1
