#!/usr/bin/env python3
"""Create and verify exact-run evidence for private-image promotion."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = Path(".github/schemas/private-image-promotion-evidence-v1.schema.json")
WORKFLOW_PATH = ".github/workflows/private-image.yml"
MAIN_REF = "refs/heads/main"
MAX_EVIDENCE_AGE = timedelta(days=7)


class EvidenceError(ValueError):
    """Raised when promotion evidence is absent, stale, or inconsistent."""


def _timestamp(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise EvidenceError(f"{name} must be an ISO-8601 timestamp with an offset") from exc
    if parsed.tzinfo is None:
        raise EvidenceError(f"{name} must be an ISO-8601 timestamp with an offset")
    return parsed.astimezone(UTC)


def _read_object(path: Path, name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvidenceError(f"{name} is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"{name} is not valid JSON: {path}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{name} must be a JSON object: {path}")
    return value


def _validate_schema(instance: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate the small JSON Schema subset used by the internal receipt."""
    required = schema.get("required")
    properties = schema.get("properties")
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or not isinstance(required, list)
        or not isinstance(properties, dict)
    ):
        raise EvidenceError("promotion evidence schema must be a strict object schema")
    missing = [field for field in required if field not in instance]
    extras = sorted(set(instance) - set(properties))
    if missing:
        raise EvidenceError(f"promotion evidence is missing required field: {missing[0]}")
    if extras:
        raise EvidenceError(f"promotion evidence has unknown field: {extras[0]}")
    for field, value in instance.items():
        rule = properties[field]
        if "const" in rule and value != rule["const"]:
            raise EvidenceError(f"promotion evidence {field} must equal {rule['const']!r}")
        expected_type = rule.get("type")
        if expected_type == "string" and not isinstance(value, str):
            raise EvidenceError(f"promotion evidence {field} must be a string")
        if expected_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise EvidenceError(f"promotion evidence {field} must be an integer")
        if "minimum" in rule and value < rule["minimum"]:
            raise EvidenceError(f"promotion evidence {field} must be at least {rule['minimum']}")
        if "pattern" in rule and re.fullmatch(rule["pattern"], value) is None:
            raise EvidenceError(f"promotion evidence {field} has an invalid format")
        if rule.get("format") == "date-time":
            _timestamp(value, f"promotion evidence {field}")


def _validate_receipt(receipt: dict[str, Any], schema_path: Path) -> None:
    schema = _read_object(schema_path, "promotion evidence schema")
    _validate_schema(receipt, schema)
    repository = receipt["workflow_repository"]
    image = receipt["image"]
    digest = receipt["digest"]
    run_id = receipt["run_id"]
    expected = {
        "subject": f"{image}@{digest}",
        "workflow_ref": f"{repository}/{WORKFLOW_PATH}@{MAIN_REF}",
        "run_url": f"https://github.com/{repository}/actions/runs/{run_id}",
        "workflow_sha": receipt["source_commit"],
    }
    for field, value in expected.items():
        if receipt[field] != value:
            raise EvidenceError(f"promotion evidence {field} does not match {value!r}")
    if image != f"ghcr.io/{repository.lower()}":
        raise EvidenceError("promotion evidence image does not match the workflow repository")


def create_receipt(
    *,
    image: str,
    digest: str,
    source_commit: str,
    source_ref: str,
    repository: str,
    workflow_ref: str,
    workflow_sha: str,
    run_id: int,
    run_attempt: int,
    run_url: str,
    recorded_at: str,
    schema_path: Path = SCHEMA,
) -> dict[str, Any]:
    """Create a successful receipt after every exact-digest smoke gate passes."""
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "furatena.private-image.promotion-evidence",
        "image": image,
        "digest": digest,
        "subject": f"{image}@{digest}",
        "source_commit": source_commit,
        "source_ref": source_ref,
        "workflow_repository": repository,
        "workflow_path": WORKFLOW_PATH,
        "workflow_ref": workflow_ref,
        "workflow_sha": workflow_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "run_url": run_url,
        "conclusion": "success",
        "recorded_at": recorded_at,
    }
    _validate_receipt(receipt, schema_path)
    return receipt


def verify_receipt(
    receipt: dict[str, Any],
    run: dict[str, Any],
    *,
    expected_image: str,
    expected_digest: str,
    expected_commit: str,
    expected_repository: str,
    expected_run_id: int,
    now: datetime,
    schema_path: Path = SCHEMA,
    max_age: timedelta = MAX_EVIDENCE_AGE,
) -> None:
    """Fail closed unless receipt and live Actions state prove one eligible run."""
    _validate_receipt(receipt, schema_path)
    expected_receipt = {
        "image": expected_image,
        "digest": expected_digest,
        "source_commit": expected_commit,
        "workflow_repository": expected_repository,
        "run_id": expected_run_id,
    }
    for field, value in expected_receipt.items():
        if receipt[field] != value:
            raise EvidenceError(f"promotion evidence {field} does not match the promotion input")

    repository = run.get("repository")
    run_repository = repository.get("full_name") if isinstance(repository, dict) else None
    expected_run = {
        "id": receipt["run_id"],
        "run_attempt": receipt["run_attempt"],
        "status": "completed",
        "conclusion": "success",
        "head_sha": receipt["source_commit"],
        "head_branch": "main",
        "path": receipt["workflow_path"],
        "html_url": receipt["run_url"],
    }
    for field, value in expected_run.items():
        if run.get(field) != value:
            raise EvidenceError(f"candidate workflow run {field} does not match {value!r}")
    if run_repository != receipt["workflow_repository"]:
        raise EvidenceError("candidate workflow run repository does not match the receipt")
    if run.get("event") not in {"push", "workflow_dispatch"}:
        raise EvidenceError("candidate workflow run event is not eligible for promotion")

    created_at = _timestamp(run.get("created_at"), "candidate workflow run created_at")
    completed_at = _timestamp(run.get("updated_at"), "candidate workflow run updated_at")
    recorded_at = _timestamp(receipt["recorded_at"], "promotion evidence recorded_at")
    now = now.astimezone(UTC)
    if recorded_at > now:
        raise EvidenceError("promotion evidence recorded_at is in the future")
    if not created_at <= recorded_at <= completed_at:
        raise EvidenceError("promotion evidence timestamp is outside the candidate workflow run")
    if completed_at > now:
        raise EvidenceError("candidate workflow completion time is in the future")
    if now - completed_at > max_age:
        raise EvidenceError(
            f"candidate workflow evidence is stale; promotion requires evidence no older than {max_age.days} days"
        )
    if now - recorded_at > max_age:
        raise EvidenceError(
            f"promotion evidence receipt is stale; promotion requires a receipt no older than {max_age.days} days"
        )


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--image", required=True)
    create.add_argument("--digest", required=True)
    create.add_argument("--source-commit", required=True)
    create.add_argument("--source-ref", required=True)
    create.add_argument("--repository", required=True)
    create.add_argument("--workflow-ref", required=True)
    create.add_argument("--workflow-sha", required=True)
    create.add_argument("--run-id", required=True, type=int)
    create.add_argument("--run-attempt", required=True, type=int)
    create.add_argument("--run-url", required=True)
    create.add_argument("--recorded-at")
    create.add_argument("--schema", type=Path, default=SCHEMA)
    create.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--receipt", type=Path, required=True)
    verify.add_argument("--run", type=Path, required=True)
    verify.add_argument("--image", required=True)
    verify.add_argument("--digest", required=True)
    verify.add_argument("--commit", required=True)
    verify.add_argument("--repository", required=True)
    verify.add_argument("--run-id", required=True, type=int)
    verify.add_argument("--now")
    verify.add_argument("--schema", type=Path, default=SCHEMA)

    args = parser.parse_args()
    try:
        if args.command == "create":
            recorded_at = args.recorded_at or datetime.now(UTC).replace(microsecond=0).isoformat()
            receipt = create_receipt(
                image=args.image,
                digest=args.digest,
                source_commit=args.source_commit,
                source_ref=args.source_ref,
                repository=args.repository,
                workflow_ref=args.workflow_ref,
                workflow_sha=args.workflow_sha,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                run_url=args.run_url,
                recorded_at=recorded_at,
                schema_path=args.schema,
            )
            _write_json(args.output, receipt)
            print(json.dumps(receipt, sort_keys=True))
        else:
            now = _timestamp(args.now, "now") if args.now else datetime.now(UTC)
            receipt = _read_object(args.receipt, "promotion evidence receipt")
            run = _read_object(args.run, "candidate workflow run")
            verify_receipt(
                receipt,
                run,
                expected_image=args.image,
                expected_digest=args.digest,
                expected_commit=args.commit,
                expected_repository=args.repository,
                expected_run_id=args.run_id,
                now=now,
                schema_path=args.schema,
            )
            print(json.dumps({"eligible": True, "run_id": args.run_id}, sort_keys=True))
    except EvidenceError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
