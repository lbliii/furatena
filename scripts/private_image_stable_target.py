#!/usr/bin/env python3
"""Verify durable stable-record ownership for private-image lifecycle targets."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from private_image_release import create_record

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


class TargetError(ValueError):
    """Raised when a lifecycle target is unknown, malformed, or unsafe."""


def _canonical_digest(value: str, *, role: str) -> str:
    if DIGEST_PATTERN.fullmatch(value) is None:
        raise TargetError(f"{role} target digest must be an exact lowercase sha256:<64 hex> value")
    return value


def _read_record(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TargetError(f"durable stable record is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TargetError(f"durable stable record is malformed JSON: {path}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise TargetError(f"durable stable record must be a JSON object: {path}")
    return value


def validate_stable_record(
    record: dict[str, Any],
    *,
    expected_image: str,
    expected_digest: str,
    expected_version: str,
    repository: str,
    role: str,
) -> str:
    """Require the exact canonical v1 stable record for an owned digest."""
    try:
        expected = create_record(
            "promote",
            image=expected_image,
            digest=expected_digest,
            commit=record["commit"],
            version=expected_version,
            rollback_digest=record["rollback"]["digest"],
            compatibility=record["compatibility"]["statement"],
            migration_notes=record["release_notes"]["migration_notes"],
            source_repository=f"https://github.com/{repository}",
            recorded_at=record["recorded_at"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TargetError(f"{role} stable record is not a schema-valid promotion record") from exc
    if record != expected:
        mismatched = next(
            (
                field
                for field in sorted(set(record) | set(expected))
                if record.get(field) != expected.get(field)
            ),
            "record",
        )
        raise TargetError(
            f"{role} stable record does not own the requested image, version, and digest: {mismatched}"
        )
    return expected["commit"]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _require_not_revoked(
    *,
    digest: str,
    repository: str,
    role: str,
    command_runner: CommandRunner,
) -> None:
    owner, name = repository.split("/", 1)
    tag = f"image-revoked-{digest.removeprefix('sha256:')}"
    query = (
        "query($owner: String!, $repository: String!, $tag: String!) { "
        "repository(owner: $owner, name: $repository) { release(tagName: $tag) { tagName } } }"
    )
    result = command_runner(
        (
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            f"owner={owner}",
            "-f",
            f"repository={name}",
            "-f",
            f"tag={tag}",
            "--jq",
            '.data.repository.release.tagName // ""',
        )
    )
    if result.returncode != 0:
        raise TargetError(
            f"could not verify {role} target revocation status; retry the lifecycle operation"
        )
    observed = result.stdout.strip()
    if observed == tag:
        raise TargetError(f"{role} target digest is revoked: {digest}")
    if observed:
        raise TargetError(
            f"{role} target revocation lookup returned an unexpected release identity"
        )


def _require_available_and_attested(
    *,
    image: str,
    digest: str,
    stable_commit: str,
    repository: str,
    role: str,
    command_runner: CommandRunner,
) -> None:
    subject = f"{image}@{digest}"
    inspected = command_runner(("docker", "buildx", "imagetools", "inspect", subject))
    if inspected.returncode != 0:
        raise TargetError(
            f"{role} target is unavailable in GHCR; restore it or choose another durable stable target"
        )
    attested = command_runner(
        (
            "gh",
            "attestation",
            "verify",
            f"oci://{subject}",
            "--repo",
            repository,
            "--signer-workflow",
            f"github.com/{repository}/.github/workflows/private-image.yml",
            "--source-digest",
            stable_commit,
            "--source-ref",
            "refs/heads/main",
            "--deny-self-hosted-runners",
        )
    )
    if attested.returncode != 0:
        raise TargetError(
            f"{role} target provenance did not verify; choose an attested durable stable target"
        )


def verify_stable_target(
    record: dict[str, Any],
    *,
    expected_image: str,
    expected_digest: str,
    expected_version: str,
    repository: str,
    role: str,
    association_only: bool = False,
    command_runner: CommandRunner = _run,
) -> None:
    """Verify durable ownership and, unless exempted, target usability."""
    if association_only and role != "revocation":
        raise TargetError(
            "association-only verification is restricted to the affected revocation digest"
        )
    canonical_digest = _canonical_digest(expected_digest, role=role)
    stable_commit = validate_stable_record(
        record,
        expected_image=expected_image,
        expected_digest=canonical_digest,
        expected_version=expected_version,
        repository=repository,
        role=role,
    )
    if association_only:
        return
    _require_not_revoked(
        digest=canonical_digest,
        repository=repository,
        role=role,
        command_runner=command_runner,
    )
    _require_available_and_attested(
        image=expected_image,
        digest=canonical_digest,
        stable_commit=stable_commit,
        repository=repository,
        role=role,
        command_runner=command_runner,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument(
        "--role", choices=("rollback", "replacement", "deprecation", "revocation"), required=True
    )
    parser.add_argument("--association-only", action="store_true")
    args = parser.parse_args()
    try:
        record = _read_record(args.record)
        verify_stable_target(
            record,
            expected_image=args.image,
            expected_digest=args.digest,
            expected_version=args.version,
            repository=args.repository,
            role=args.role,
            association_only=args.association_only,
        )
    except TargetError as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "association_only": args.association_only,
                "digest": args.digest,
                "eligible": True,
                "role": args.role,
                "version": args.version,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
