#!/usr/bin/env python3
"""Create deterministic candidate, promotion, and revocation image records."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


def create_record(
    operation: str,
    *,
    image: str,
    digest: str,
    commit: str = "",
    version: str = "",
    reason: str = "",
    replacement_digest: str = "",
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Create a validated lifecycle record without mutating registry state."""
    image = _image(image)
    digest = _digest(digest)
    timestamp = recorded_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    record: dict[str, Any] = {
        "schema_version": 1,
        "product": "furatena",
        "distribution": "private-image",
        "operation": operation,
        "image": image,
        "digest": digest,
        "subject": f"{image}@{digest}",
        "recorded_at": timestamp,
    }
    if operation == "candidate":
        record.update(
            {
                "channel": "candidate",
                "status": "active",
                "commit": _commit(commit),
            }
        )
    elif operation == "promote":
        record.update(
            {
                "channel": "stable",
                "status": "active",
                "version": _version(version),
                "commit": _commit(commit),
            }
        )
    elif operation == "revoke":
        if not reason.strip():
            raise ValueError("revocation requires a non-empty reason")
        record.update(
            {
                "status": "revoked",
                "version": _version(version),
                "reason": reason.strip(),
                "replacement_digest": (
                    _digest(replacement_digest) if replacement_digest.strip() else None
                ),
            }
        )
    else:
        raise ValueError(f"unsupported operation: {operation!r}")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("candidate", "promote", "revoke"))
    parser.add_argument("--image", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--commit", default="")
    parser.add_argument("--version", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--replacement-digest", default="")
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
            recorded_at=args.recorded_at,
        )
    except ValueError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
