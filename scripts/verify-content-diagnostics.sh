#!/usr/bin/env bash
set -euo pipefail

SUBJECT=${1:?usage: verify-content-diagnostics.sh IMAGE@sha256:DIGEST}
CONTENT_REPOSITORY=${FURA_DIAGNOSTIC_CONTENT_REPOSITORY:-https://github.com/lbliii/furatena-content-starter.git}
CONTENT_REF=${FURA_DIAGNOSTIC_CONTENT_REF:-main}
RUN_ID=${GITHUB_RUN_ID:-$$}
EVIDENCE_DIR=${FURA_DIAGNOSTIC_EVIDENCE_DIR:-diagnostic-evidence}
WORK_DIR=$(mktemp -d)
READ_ONLY_VOLUME="furatena-diagnostics-read-only-$RUN_ID"
MISSING_VOLUME="furatena-diagnostics-missing-$RUN_ID"
QUOTA_VOLUME="furatena-diagnostics-quota-$RUN_ID"
CREDENTIAL_USER=diagnostic-user-473
CREDENTIAL_SECRET=diagnostic-secret-473

mkdir -p "$EVIDENCE_DIR"
printf '{"schema_version":1,"status":"running"}\n' > "$EVIDENCE_DIR/summary.json"

cleanup() {
  docker volume rm --force "$READ_ONLY_VOLUME" "$MISSING_VOLUME" "$QUOTA_VOLUME" \
    >/dev/null 2>&1 || true
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

assert_failure() {
  local name=$1
  shift
  local log="$WORK_DIR/$name.log"
  if timeout 300 docker run --rm "$@" "$SUBJECT" > "$log" 2>&1; then
    echo "$name unexpectedly started successfully" >&2
    cat "$log" >&2
    return 1
  fi
  if grep -F "Traceback (most recent call last)" "$log" >/dev/null; then
    echo "$name emitted an internal Python traceback" >&2
    cat "$log" >&2
    return 1
  fi
}

assert_log_contains() {
  local name=$1
  local expected=$2
  if ! grep -F "$expected" "$WORK_DIR/$name.log" >/dev/null; then
    echo "$name did not report the expected diagnostic: $expected" >&2
    cat "$WORK_DIR/$name.log" >&2
    return 1
  fi
}

assert_log_excludes() {
  local name=$1
  local forbidden=$2
  if grep -F "$forbidden" "$WORK_DIR/$name.log" >/dev/null; then
    echo "$name leaked forbidden credential material" >&2
    return 1
  fi
}

docker volume create "$READ_ONLY_VOLUME" >/dev/null
docker volume create "$MISSING_VOLUME" >/dev/null
docker volume create "$QUOTA_VOLUME" >/dev/null

common=(
  --read-only
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=268435456
  --env FURA_CONTENT_REPOSITORY="$CONTENT_REPOSITORY"
  --env FURA_CONTENT_REF="$CONTENT_REF"
  --env FURA_CONTENT_STATE_ROOT=/data/furatena
)

assert_failure read-only-state \
  "${common[@]}" \
  --mount "type=volume,source=$READ_ONLY_VOLUME,target=/data/furatena,readonly"
assert_log_contains read-only-state "FURA_CONTENT_STATE_ROOT=/data/furatena"
assert_log_contains read-only-state "/data/furatena/leases"
assert_log_contains read-only-state "writable Railway volume at /data/furatena"

assert_failure missing-subdirectory \
  "${common[@]}" \
  --mount "type=volume,source=$MISSING_VOLUME,target=/data/furatena" \
  --env FURA_CONTENT_SUBDIRECTORY=missing-docs-473
assert_log_contains missing-subdirectory "FURA_CONTENT_SUBDIRECTORY=missing-docs-473"
assert_log_contains missing-subdirectory "correct the configured subdirectory and retry"

assert_failure quota-exhaustion \
  "${common[@]}" \
  --mount "type=volume,source=$QUOTA_VOLUME,target=/data/furatena" \
  --env FURA_CONTENT_MAX_BYTES=1
assert_log_contains quota-exhaustion "content exceeds FURA_CONTENT_MAX_BYTES=1"

assert_failure credential-rejection \
  --read-only \
  --network none \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=268435456 \
  --env "FURA_CONTENT_REPOSITORY=https://$CREDENTIAL_USER:$CREDENTIAL_SECRET@github.com/lbliii/furatena-content-starter.git"
assert_log_contains credential-rejection "FURA_CONTENT_REPOSITORY must be an HTTPS public Git URL"
assert_log_contains credential-rejection "without credentials"
assert_log_excludes credential-rejection "$CREDENTIAL_USER"
assert_log_excludes credential-rejection "$CREDENTIAL_SECRET"

SUBJECT_JSON=$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$SUBJECT")
printf '{\n  "schema_version": 1,\n  "subject": %s,\n  "read_only_state": true,\n  "missing_subdirectory": true,\n  "quota_exhaustion": true,\n  "credential_rejection_sanitized": true,\n  "tracebacks_absent": true,\n  "failed_startup_exit_nonzero": true\n}\n' \
  "$SUBJECT_JSON" > "$EVIDENCE_DIR/summary.json"
