#!/usr/bin/env sh
set -eu

RUNTIME_UID="${FURA_RUNTIME_UID:-65532}"
RUNTIME_GID="${FURA_RUNTIME_GID:-65532}"
CONTENT_STATE_ROOT="${FURA_CONTENT_STATE_ROOT:-/data/furatena}"
HOME="${HOME:-/tmp/furatena-home}"
XDG_CACHE_HOME="${XDG_CACHE_HOME:-/tmp/furatena-cache}"
export HOME XDG_CACHE_HOME

case "$RUNTIME_UID" in
  "" | *[!0-9]*)
    echo "Furatena startup failed: FURA_RUNTIME_UID and FURA_RUNTIME_GID must be numeric." >&2
    exit 78
    ;;
esac
case "$RUNTIME_GID" in
  "" | *[!0-9]*)
    echo "Furatena startup failed: FURA_RUNTIME_UID and FURA_RUNTIME_GID must be numeric." >&2
    exit 78
    ;;
esac
if [ "$RUNTIME_UID" -eq 0 ] || [ "$RUNTIME_GID" -eq 0 ]; then
  echo "Furatena startup failed: the application runtime UID and GID must be nonzero." >&2
  exit 77
fi

if [ "$(id -u)" -eq 0 ]; then
  if [ "${RAILWAY_RUN_UID:-}" != 0 ]; then
    echo "Furatena startup failed: root bootstrap requires the explicit Railway RAILWAY_RUN_UID=0 volume compatibility setting." >&2
    exit 77
  fi
  if [ "$CONTENT_STATE_ROOT" != /data/furatena ] || [ "${RAILWAY_VOLUME_MOUNT_PATH:-}" != "$CONTENT_STATE_ROOT" ]; then
    echo "Furatena startup failed: RAILWAY_VOLUME_MOUNT_PATH and FURA_CONTENT_STATE_ROOT must both be /data/furatena." >&2
    exit 78
  fi
  if [ -L "$CONTENT_STATE_ROOT" ]; then
    echo "Furatena startup failed: the managed-content volume path cannot be a symbolic link: $CONTENT_STATE_ROOT." >&2
    exit 78
  fi
  mkdir -p "$CONTENT_STATE_ROOT"
  ownership_marker="$CONTENT_STATE_ROOT/.furatena-runtime-owner-v1"
  if [ -L "$ownership_marker" ]; then
    echo "Furatena startup failed: the volume ownership marker cannot be a symbolic link." >&2
    exit 78
  fi
  chown "$RUNTIME_UID:$RUNTIME_GID" "$CONTENT_STATE_ROOT"
  if [ ! -e "$ownership_marker" ]; then
    chown -R -h "$RUNTIME_UID:$RUNTIME_GID" "$CONTENT_STATE_ROOT"
    : > "$ownership_marker"
    chown "$RUNTIME_UID:$RUNTIME_GID" "$ownership_marker"
  fi
  privilege_drop="$(command -v gosu || true)"
  if [ -z "$privilege_drop" ]; then
    echo "Furatena startup failed: the image is missing its privilege-drop helper." >&2
    exit 70
  fi
  exec "$privilege_drop" "$RUNTIME_UID:$RUNTIME_GID" "$0" "$@"
fi

if [ "$(id -u)" -ne "$RUNTIME_UID" ] || [ "$(id -g)" -ne "$RUNTIME_GID" ]; then
  echo "Furatena startup failed: runtime identity must be uid=$RUNTIME_UID gid=$RUNTIME_GID." >&2
  exit 77
fi

mkdir -p "$HOME" "$XDG_CACHE_HOME"
if [ -n "${FURA_CONTENT_REPOSITORY:-}" ]; then
  write_probe="$CONTENT_STATE_ROOT/.furatena-write-probe.$$"
  if ! (umask 077 && : > "$write_probe"); then
    echo "Furatena startup failed: FURA_CONTENT_STATE_ROOT=$CONTENT_STATE_ROOT is not writable by uid=$RUNTIME_UID. Mount the Railway volume at /data/furatena and set RAILWAY_RUN_UID=0 for the ownership bootstrap." >&2
    exit 73
  fi
  rm -f "$write_probe"
fi

# Import the server stack before checking. A non-free-threading-safe extension
# can enable the GIL when imported, which would make a bare interpreter check a
# false positive.
python -c 'import os, sys; from furatena.catalog.docs_app import DocsApp; expected = int(os.environ["FURA_RUNTIME_UID"]); assert os.getuid() == expected != 0, "Furatena requires its unprivileged runtime identity"; assert os.path.realpath(sys.executable).startswith("/opt/python/"), "Furatena requires the application-owned interpreter"; assert not sys._is_gil_enabled(), "Furatena requires a GIL-disabled runtime"'

APP_ROOT=/app/app
if [ -n "${FURA_CONTENT_REPOSITORY:-}" ]; then
  fura content reconcile
  APP_ROOT="$CONTENT_STATE_ROOT/active/source/${FURA_CONTENT_SUBDIRECTORY:-app}"
  FURA_FROZEN_DIR="$CONTENT_STATE_ROOT/active/frozen"
  export FURA_FROZEN_DIR
fi

exec fura --app-root "$APP_ROOT" serve \
  --preview \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1
