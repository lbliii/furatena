#!/usr/bin/env sh
set -eu

# Import the server stack before checking. A non-free-threading-safe extension
# can enable the GIL when imported, which would make a bare interpreter check a
# false positive.
python -c 'import sys; from furatena.catalog.docs_app import DocsApp; assert not sys._is_gil_enabled(), "Furatena requires a GIL-disabled runtime"'

# Temporary mitigation for active HTTP/2 responses being closed at Pounce's
# five-second keep-alive default. Remove after lbliii/pounce#231 and #232 ship.
export FURA_KEEP_ALIVE_TIMEOUT="${FURA_KEEP_ALIVE_TIMEOUT:-75}"

exec fura --app-root /app/app serve \
  --preview \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1
