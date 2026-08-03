#!/usr/bin/env bash
set -euo pipefail

SUBJECT=${1:?usage: verify-unprivileged-image.sh IMAGE@sha256:DIGEST}
CONTENT_REPOSITORY=${FURA_CONFORMANCE_CONTENT_REPOSITORY:-https://github.com/lbliii/furatena-content-starter.git}
FIRST_REF=${FURA_CONFORMANCE_FIRST_REF:-main}
SECOND_REF=${FURA_CONFORMANCE_SECOND_REF:-conformance-v2}
PORT=${FURA_CONFORMANCE_PORT:-18001}
RUN_ID=${GITHUB_RUN_ID:-$$}
CONTAINER="furatena-unprivileged-$RUN_ID"
VOLUME="furatena-unprivileged-$RUN_ID"
EVIDENCE_DIR=${FURA_CONFORMANCE_EVIDENCE_DIR:-runtime-evidence}
TOKEN=furatena-runtime-conformance-token-0000000000000000

mkdir -p "$EVIDENCE_DIR"

cleanup() {
  docker rm --force "$CONTAINER" >/dev/null 2>&1 || true
  docker volume rm --force "$VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait_for_readiness() {
  for _attempt in $(seq 1 120); do
    if curl --fail --silent "http://127.0.0.1:$PORT/readyz" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  docker logs "$CONTAINER"
  return 1
}

assert_runtime_identity() {
  local runtime_uid runtime_executable unexpected_owner
  if ! runtime_uid=$(docker exec --user 0:0 "$CONTAINER" awk '/^Uid:/{print $2}' /proc/1/status); then
    echo "failed to inspect runtime uid" >&2
    docker logs "$CONTAINER" >&2
    return 1
  fi
  if [ "$runtime_uid" != 65532 ]; then
    echo "unexpected runtime uid: $runtime_uid (expected 65532)" >&2
    return 1
  fi
  if ! runtime_executable=$(
    docker exec --user 65532:65532 "$CONTAINER" \
      python -c 'import os, sys; print(os.path.realpath(sys.executable))'
  ); then
    echo "failed to inspect runtime executable" >&2
    docker logs "$CONTAINER" >&2
    return 1
  fi
  case "$runtime_executable" in
    /opt/python/*) ;;
    *)
      echo "unexpected runtime executable: $runtime_executable" >&2
      return 1
      ;;
  esac
  if ! unexpected_owner=$(
    docker exec --user 0:0 "$CONTAINER" \
      find /data/furatena -xdev ! -uid 65532 -print -quit
  ); then
    echo "failed to inspect managed-volume ownership" >&2
    docker logs "$CONTAINER" >&2
    return 1
  fi
  if [ -n "$unexpected_owner" ]; then
    echo "unexpected managed-volume owner: $unexpected_owner" >&2
    docker exec --user 0:0 "$CONTAINER" ls -ldn "$unexpected_owner" >&2
    return 1
  fi
}

start_generation() {
  local ref=$1
  docker rm --force "$CONTAINER" >/dev/null 2>&1 || true
  docker run --detach \
    --name "$CONTAINER" \
    --user 0:0 \
    --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=268435456 \
    --mount "type=volume,source=$VOLUME,target=/data/furatena" \
    --publish "$PORT:8000" \
    --env RAILWAY_RUN_UID=0 \
    --env RAILWAY_VOLUME_MOUNT_PATH=/data/furatena \
    --env FURA_CONTENT_STATE_ROOT=/data/furatena \
    --env FURA_CONTENT_REPOSITORY="$CONTENT_REPOSITORY" \
    --env FURA_CONTENT_REF="$ref" \
    --env FURA_CONTENT_SUBDIRECTORY=app \
    --env FURA_CONTENT_REFRESH_TOKEN="$TOKEN" \
    --env FURA_CONTENT_RESTART_AFTER_PROMOTION=0 \
    --env FURA_IMAGE_DIGEST="$SUBJECT" \
    "$SUBJECT" >/dev/null
  wait_for_readiness
  echo "readiness passed for ref=$ref"
  assert_runtime_identity
  echo "runtime identity passed for ref=$ref"
}

capture_status() {
  local name=$1
  if ! curl --fail --silent "http://127.0.0.1:$PORT/_fura/content/status" \
    > "$EVIDENCE_DIR/$name.json"; then
    echo "failed to capture managed-content status: $name" >&2
    docker logs "$CONTAINER" >&2
    return 1
  fi
}

generation_from() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["active_generation"])' "$1"
}

docker volume create "$VOLUME" >/dev/null

start_generation "$FIRST_REF"
capture_status initial
FIRST_GENERATION=$(generation_from "$EVIDENCE_DIR/initial.json")

start_generation "$SECOND_REF"
capture_status refreshed
SECOND_GENERATION=$(generation_from "$EVIDENCE_DIR/refreshed.json")
test "$SECOND_GENERATION" != "$FIRST_GENERATION"

curl --fail --silent --request POST \
  --header "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:$PORT/_fura/content/rollback" \
  > "$EVIDENCE_DIR/rollback.json"
capture_status rolled-back
test "$(generation_from "$EVIDENCE_DIR/rolled-back.json")" = "$FIRST_GENERATION"

start_generation "$SECOND_REF"
capture_status restart-after-rollback
test "$(generation_from "$EVIDENCE_DIR/restart-after-rollback.json")" = "$FIRST_GENERATION"

docker rm --force "$CONTAINER" >/dev/null
docker run --rm --user 0:0 \
  --mount "type=volume,source=$VOLUME,target=/data/furatena" \
  --entrypoint /bin/sh "$SUBJECT" -c 'rm -f /data/furatena/active'
start_generation "$SECOND_REF"
capture_status recovered-from-last-known-good
test "$(generation_from "$EVIDENCE_DIR/recovered-from-last-known-good.json")" = "$SECOND_GENERATION"

python3 - "$EVIDENCE_DIR/summary.json" "$SUBJECT" "$FIRST_GENERATION" "$SECOND_GENERATION" <<'PY'
import json
import sys

path, subject, first, second = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "schema_version": 1,
            "subject": subject,
            "runtime_uid": 65532,
            "runtime_gid": 65532,
            "read_only_root": True,
            "first_generation": first,
            "second_generation": second,
            "rollback_verified": True,
            "restart_hold_verified": True,
            "last_known_good_recovery_verified": True,
        },
        handle,
        indent=2,
        sort_keys=True,
    )
    handle.write("\n")
PY
