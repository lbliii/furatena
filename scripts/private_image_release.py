#!/usr/bin/env python3
"""Create deterministic private-image lifecycle and adopter-update records."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
VERSION_PATTERN = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
IMAGE_PATTERN = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9._/-]*[a-z0-9]$")


def _digest(value: str) -> str:
    normalized = value.strip().lower()
    if DIGEST_PATTERN.fullmatch(normalized) is None:
        raise ValueError("digest must be an exact lowercase sha256:<64 hex> value")
    return normalized


def _commit(value: str) -> str:
    normalized = value.strip().lower()
    if COMMIT_PATTERN.fullmatch(normalized) is None:
        raise ValueError("commit must be an exact forty-character lowercase Git SHA")
    return normalized


def _version(value: str) -> str:
    normalized = value.strip()
    if VERSION_PATTERN.fullmatch(normalized) is None:
        raise ValueError("version must be an exact MAJOR.MINOR.PATCH value")
    return normalized


def _image(value: str) -> str:
    normalized = value.strip().lower()
    if IMAGE_PATTERN.fullmatch(normalized) is None or "@" in normalized or ":" in normalized[8:]:
        raise ValueError("image must be an untagged lowercase ghcr.io registry path")
    return normalized


def _text(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    if len(normalized) > 2_000:
        raise ValueError(f"{name} must not exceed 2,000 characters")
    return normalized


def _repository(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or len([part for part in parsed.path.split("/") if part]) != 2
    ):
        raise ValueError("source repository must be an HTTPS github.com owner/repository URL")
    return urlunsplit(("https", "github.com", parsed.path.rstrip("/"), "", ""))


def _timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0).isoformat()
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("recorded-at must be an ISO-8601 timestamp with an offset") from exc
    if parsed.tzinfo is None:
        raise ValueError("recorded-at must be an ISO-8601 timestamp with an offset")
    return normalized


def _remediation(image: str, replacement_digest: str | None, support_url: str) -> dict[str, str]:
    if replacement_digest is not None:
        return {
            "action": "select_replacement_digest",
            "subject": f"{image}@{replacement_digest}",
            "support_url": support_url,
        }
    return {
        "action": "stop_new_deployments_and_contact_support",
        "support_url": support_url,
    }


def create_record(
    operation: str,
    *,
    image: str,
    digest: str,
    commit: str = "",
    version: str = "",
    reason: str = "",
    replacement_digest: str = "",
    rollback_digest: str = "",
    compatibility: str = "",
    migration_notes: str = "",
    support_ends_at: str = "",
    source_repository: str = "",
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Create a validated lifecycle record without mutating registry state."""
    image = _image(image)
    digest = _digest(digest)
    timestamp = _timestamp(recorded_at)
    record: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "furatena.private-image.lifecycle",
        "product": "furatena",
        "distribution": "private-image",
        "operation": operation,
        "image": image,
        "digest": digest,
        "subject": f"{image}@{digest}",
        "recorded_at": timestamp,
    }
    if operation in {"development", "candidate"}:
        record.update(
            {
                "channel": operation,
                "status": "active",
                "commit": _commit(commit),
            }
        )
    elif operation == "promote":
        commit = _commit(commit)
        repository = _repository(source_repository)
        rollback = _digest(rollback_digest)
        if rollback == digest:
            raise ValueError("rollback digest must differ from the promoted digest")
        record.update(
            {
                "channel": "stable",
                "status": "active",
                "version": _version(version),
                "commit": commit,
                "compatibility": {
                    "statement": _text(compatibility, "compatibility statement"),
                    "content_contract": {
                        "version": 1,
                        "url": f"{repository}/blob/{commit}/docs/DCP.md",
                    },
                    "configuration_contract": {
                        "version": 1,
                        "url": (
                            f"{repository}/blob/{commit}/content/furatena/docs/reference/"
                            "generated-cli-config.md"
                        ),
                    },
                },
                "release_notes": {
                    "changelog_url": f"{repository}/blob/{commit}/CHANGELOG.md",
                    "migration_notes": _text(migration_notes, "migration notes"),
                    "support_policy_url": f"{repository}/blob/{commit}/docs/COMPATIBILITY.md",
                },
                "rollback": {
                    "digest": rollback,
                    "subject": f"{image}@{rollback}",
                },
            }
        )
    elif operation in {"deprecate", "revoke"}:
        repository = _repository(source_repository)
        replacement = _digest(replacement_digest) if replacement_digest.strip() else None
        if replacement == digest:
            raise ValueError("replacement digest must differ from the affected digest")
        support_url = f"{repository}/blob/main/docs/COMPATIBILITY.md"
        record.update(
            {
                "channel": "deprecated" if operation == "deprecate" else "revoked",
                "status": "deprecated" if operation == "deprecate" else "revoked",
                "version": _version(version),
                "reason": _text(reason, f"{operation} reason"),
                "affected_digests": [digest],
                "replacement_digest": replacement,
                "remediation": _remediation(image, replacement, support_url),
            }
        )
        if operation == "deprecate":
            if not support_ends_at.strip():
                raise ValueError("deprecation requires support-ends-at")
            support_end = _timestamp(support_ends_at)
            if datetime.fromisoformat(support_end.replace("Z", "+00:00")) <= datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            ):
                raise ValueError("support-ends-at must be later than recorded-at")
            record["support_ends_at"] = support_end
    else:
        raise ValueError(f"unsupported operation: {operation!r}")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("development", "candidate", "promote", "deprecate", "revoke")
    )
    parser.add_argument("--image", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--commit", default="")
    parser.add_argument("--version", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--replacement-digest", default="")
    parser.add_argument("--rollback-digest", default="")
    parser.add_argument("--compatibility", default="")
    parser.add_argument("--migration-notes", default="")
    parser.add_argument("--support-ends-at", default="")
    parser.add_argument("--source-repository", default="")
    parser.add_argument("--recorded-at")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        record = create_record(
            args.operation,
            image=args.image,
            digest=args.digest,
            commit=args.commit,
            version=args.version,
            reason=args.reason,
            replacement_digest=args.replacement_digest,
            rollback_digest=args.rollback_digest,
            compatibility=args.compatibility,
            migration_notes=args.migration_notes,
            support_ends_at=args.support_ends_at,
            source_repository=args.source_repository,
            recorded_at=args.recorded_at,
        )
    except ValueError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
