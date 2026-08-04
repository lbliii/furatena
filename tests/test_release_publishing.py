"""Private-image publication, promotion, and revocation contracts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "private_image_release.py"
EVIDENCE_SCRIPT = ROOT / "scripts" / "private_image_promotion_evidence.py"
EVIDENCE_SCHEMA = ROOT / ".github" / "schemas" / "private-image-promotion-evidence-v1.schema.json"
WORKFLOW = ROOT / ".github" / "workflows" / "private-image.yml"
DIGEST = f"sha256:{'a' * 64}"
REPLACEMENT = f"sha256:{'b' * 64}"
COMMIT = "c" * 40
REPOSITORY = "https://github.com/lbliii/furatena"
SCHEMAS = ROOT / "src" / "furatena" / "catalog" / "schemas" / "private-image" / "v1"


def _run_record(tmp_path: Path, operation: str, *arguments: str):
    output = tmp_path / f"{operation}.json"
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            operation,
            "--image",
            "ghcr.io/lbliii/furatena",
            "--digest",
            DIGEST,
            "--recorded-at",
            "2026-07-14T12:00:00+00:00",
            "--output",
            str(output),
            *arguments,
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return completed, output


def _validate_record(record: dict[str, object], *, feed_entry: bool) -> None:
    checker = FormatChecker()
    lifecycle_schema = json.loads(
        (SCHEMAS / "lifecycle-record.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(lifecycle_schema)
    Draft202012Validator(lifecycle_schema, format_checker=checker).validate(record)
    if feed_entry:
        feed_schema = json.loads(
            (SCHEMAS / "update-feed-entry.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(feed_schema)
        Draft202012Validator(feed_schema, format_checker=checker).validate(record)


def _promotion_receipt() -> dict[str, object]:
    run_id = 123456
    return {
        "schema_version": 1,
        "record_type": "furatena.private-image.promotion-evidence",
        "image": "ghcr.io/lbliii/furatena",
        "digest": DIGEST,
        "subject": f"ghcr.io/lbliii/furatena@{DIGEST}",
        "source_commit": COMMIT,
        "source_ref": "refs/heads/main",
        "workflow_repository": "lbliii/furatena",
        "workflow_path": ".github/workflows/private-image.yml",
        "workflow_ref": ("lbliii/furatena/.github/workflows/private-image.yml@refs/heads/main"),
        "workflow_sha": COMMIT,
        "run_id": run_id,
        "run_attempt": 2,
        "run_url": f"https://github.com/lbliii/furatena/actions/runs/{run_id}",
        "conclusion": "success",
        "recorded_at": "2026-08-04T12:20:00+00:00",
    }


def _candidate_run() -> dict[str, object]:
    receipt = _promotion_receipt()
    return {
        "id": receipt["run_id"],
        "run_attempt": receipt["run_attempt"],
        "status": "completed",
        "conclusion": "success",
        "head_sha": COMMIT,
        "head_branch": "main",
        "event": "push",
        "path": ".github/workflows/private-image.yml",
        "html_url": receipt["run_url"],
        "created_at": "2026-08-04T12:00:00Z",
        "updated_at": "2026-08-04T12:21:00Z",
        "repository": {"full_name": "lbliii/furatena"},
    }


def _verify_promotion_evidence(
    tmp_path: Path,
    *,
    receipt: dict[str, object] | None = None,
    run: dict[str, object] | None = None,
    digest: str = DIGEST,
    commit: str = COMMIT,
) -> subprocess.CompletedProcess[str]:
    receipt_path = tmp_path / "promotion-evidence.json"
    run_path = tmp_path / "candidate-run.json"
    if receipt is not None:
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    run_path.write_text(json.dumps(run or _candidate_run()), encoding="utf-8")
    return subprocess.run(
        (
            sys.executable,
            str(EVIDENCE_SCRIPT),
            "verify",
            "--receipt",
            str(receipt_path),
            "--run",
            str(run_path),
            "--image",
            "ghcr.io/lbliii/furatena",
            "--digest",
            digest,
            "--commit",
            commit,
            "--repository",
            "lbliii/furatena",
            "--run-id",
            "123456",
            "--now",
            "2026-08-04T12:30:00Z",
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_candidate_record_is_digest_addressed_and_deterministic(tmp_path: Path) -> None:
    completed, output = _run_record(tmp_path, "candidate", "--commit", COMMIT)

    assert completed.returncode == 0, completed.stderr
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record == {
        "channel": "candidate",
        "commit": COMMIT,
        "digest": DIGEST,
        "distribution": "private-image",
        "image": "ghcr.io/lbliii/furatena",
        "operation": "candidate",
        "product": "furatena",
        "recorded_at": "2026-07-14T12:00:00+00:00",
        "record_type": "furatena.private-image.lifecycle",
        "schema_version": 1,
        "status": "active",
        "subject": f"ghcr.io/lbliii/furatena@{DIGEST}",
    }
    _validate_record(record, feed_entry=False)


def test_development_channel_is_explicit_but_not_a_feed_entry(tmp_path: Path) -> None:
    completed, output = _run_record(tmp_path, "development", "--commit", COMMIT)

    assert completed.returncode == 0, completed.stderr
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["channel"] == "development"
    assert record["digest"] == DIGEST
    _validate_record(record, feed_entry=False)


def test_promotion_keeps_the_candidate_digest(tmp_path: Path) -> None:
    completed, output = _run_record(
        tmp_path,
        "promote",
        "--commit",
        COMMIT,
        "--version",
        "1.2.3",
        "--rollback-digest",
        REPLACEMENT,
        "--compatibility",
        "Compatible with the v1 content and configuration contracts.",
        "--migration-notes",
        "No adopter configuration migration is required.",
        "--source-repository",
        REPOSITORY,
    )

    assert completed.returncode == 0, completed.stderr
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["digest"] == DIGEST
    assert record["subject"].endswith(f"@{DIGEST}")
    assert record["channel"] == "stable"
    assert record["version"] == "1.2.3"
    assert record["commit"] == COMMIT
    assert record["rollback"] == {
        "digest": REPLACEMENT,
        "subject": f"ghcr.io/lbliii/furatena@{REPLACEMENT}",
    }
    assert record["compatibility"]["content_contract"]["version"] == 1
    assert record["compatibility"]["configuration_contract"]["version"] == 1
    assert record["release_notes"]["changelog_url"].endswith(f"/{COMMIT}/CHANGELOG.md")
    assert record["release_notes"]["migration_notes"].startswith("No adopter")

    _validate_record(record, feed_entry=True)
    assert all(
        forbidden not in json.dumps(record).lower()
        for forbidden in ("credential", "password", "token", "secret")
    )


def test_deprecation_names_support_window_and_replacement(tmp_path: Path) -> None:
    completed, output = _run_record(
        tmp_path,
        "deprecate",
        "--reason",
        "Upgrade before the compatibility window closes.",
        "--version",
        "1.2.3",
        "--replacement-digest",
        REPLACEMENT,
        "--support-ends-at",
        "2026-12-31T23:59:59+00:00",
        "--source-repository",
        REPOSITORY,
    )

    assert completed.returncode == 0, completed.stderr
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["channel"] == "deprecated"
    assert record["status"] == "deprecated"
    assert record["affected_digests"] == [DIGEST]
    assert record["support_ends_at"] == "2026-12-31T23:59:59+00:00"
    assert record["remediation"]["subject"].endswith(f"@{REPLACEMENT}")
    _validate_record(record, feed_entry=True)


def test_revocation_preserves_subject_and_names_replacement(tmp_path: Path) -> None:
    completed, output = _run_record(
        tmp_path,
        "revoke",
        "--reason",
        "critical runtime vulnerability",
        "--version",
        "1.2.3",
        "--replacement-digest",
        REPLACEMENT,
        "--source-repository",
        REPOSITORY,
    )

    assert completed.returncode == 0, completed.stderr
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["status"] == "revoked"
    assert record["digest"] == DIGEST
    assert record["replacement_digest"] == REPLACEMENT
    assert record["reason"] == "critical runtime vulnerability"
    assert record["version"] == "1.2.3"
    assert record["channel"] == "revoked"
    assert record["affected_digests"] == [DIGEST]
    assert record["remediation"]["action"] == "select_replacement_digest"
    _validate_record(record, feed_entry=True)


def test_promotion_requires_distinct_digest_pinned_rollback_and_release_contracts(
    tmp_path: Path,
) -> None:
    completed, output = _run_record(
        tmp_path,
        "promote",
        "--commit",
        COMMIT,
        "--version",
        "1.2.3",
        "--rollback-digest",
        DIGEST,
        "--compatibility",
        "Compatible.",
        "--migration-notes",
        "No migration required.",
        "--source-repository",
        REPOSITORY,
    )

    assert completed.returncode != 0
    assert "rollback digest must differ" in completed.stderr
    assert not output.exists()


def test_lifecycle_records_reject_ambiguous_identity(tmp_path: Path) -> None:
    output = tmp_path / "invalid.json"
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "promote",
            "--image",
            "ghcr.io/lbliii/furatena:latest",
            "--digest",
            "latest",
            "--commit",
            "main",
            "--version",
            "v1",
            "--output",
            str(output),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode != 0
    assert not output.exists()


def test_promotion_evidence_is_schema_valid_and_verifies_exact_successful_run(
    tmp_path: Path,
) -> None:
    output = tmp_path / "promotion-evidence.json"
    completed = subprocess.run(
        (
            sys.executable,
            str(EVIDENCE_SCRIPT),
            "create",
            "--image",
            "ghcr.io/lbliii/furatena",
            "--digest",
            DIGEST,
            "--source-commit",
            COMMIT,
            "--source-ref",
            "refs/heads/main",
            "--repository",
            "lbliii/furatena",
            "--workflow-ref",
            "lbliii/furatena/.github/workflows/private-image.yml@refs/heads/main",
            "--workflow-sha",
            COMMIT,
            "--run-id",
            "123456",
            "--run-attempt",
            "2",
            "--run-url",
            "https://github.com/lbliii/furatena/actions/runs/123456",
            "--recorded-at",
            "2026-08-04T12:20:00+00:00",
            "--output",
            str(output),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(output.read_text(encoding="utf-8"))
    schema = json.loads(EVIDENCE_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(receipt)
    verified = _verify_promotion_evidence(tmp_path, receipt=receipt)
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout) == {"eligible": True, "run_id": 123456}


def test_promotion_evidence_fails_closed_when_receipt_is_missing(tmp_path: Path) -> None:
    completed = _verify_promotion_evidence(tmp_path)

    assert completed.returncode != 0
    assert "promotion evidence receipt is missing" in completed.stderr


@pytest.mark.parametrize(
    ("status", "conclusion", "message"),
    (
        ("completed", "failure", "conclusion does not match 'success'"),
        ("completed", "cancelled", "conclusion does not match 'success'"),
        ("in_progress", None, "status does not match 'completed'"),
    ),
)
def test_promotion_evidence_rejects_unsuccessful_or_incomplete_run(
    tmp_path: Path,
    status: str,
    conclusion: str | None,
    message: str,
) -> None:
    run = _candidate_run()
    run.update(status=status, conclusion=conclusion)

    completed = _verify_promotion_evidence(tmp_path, receipt=_promotion_receipt(), run=run)

    assert completed.returncode != 0
    assert message in completed.stderr


def test_promotion_evidence_rejects_stale_run(tmp_path: Path) -> None:
    receipt = _promotion_receipt()
    receipt["recorded_at"] = "2026-07-20T12:20:00Z"
    run = _candidate_run()
    run["created_at"] = "2026-07-20T12:00:00Z"
    run["updated_at"] = "2026-07-20T12:21:00Z"

    completed = _verify_promotion_evidence(tmp_path, receipt=receipt, run=run)

    assert completed.returncode != 0
    assert "evidence is stale" in completed.stderr


def test_promotion_evidence_rejects_stale_receipt_after_fresh_run_update(
    tmp_path: Path,
) -> None:
    receipt = _promotion_receipt()
    receipt["recorded_at"] = "2026-07-20T12:20:00Z"
    run = _candidate_run()
    run["created_at"] = "2026-07-20T12:00:00Z"

    completed = _verify_promotion_evidence(tmp_path, receipt=receipt, run=run)

    assert completed.returncode != 0
    assert "promotion evidence receipt is stale" in completed.stderr


def test_promotion_evidence_rejects_future_receipt(tmp_path: Path) -> None:
    receipt = _promotion_receipt()
    receipt["recorded_at"] = "2026-08-04T12:31:00Z"
    run = _candidate_run()
    run["updated_at"] = "2026-08-04T12:32:00Z"

    completed = _verify_promotion_evidence(tmp_path, receipt=receipt, run=run)

    assert completed.returncode != 0
    assert "promotion evidence recorded_at is in the future" in completed.stderr


def test_promotion_evidence_accepts_receipt_at_exact_freshness_boundary(
    tmp_path: Path,
) -> None:
    receipt = _promotion_receipt()
    receipt["recorded_at"] = "2026-07-28T12:30:00Z"
    run = _candidate_run()
    run["created_at"] = "2026-07-28T12:00:00Z"

    completed = _verify_promotion_evidence(tmp_path, receipt=receipt, run=run)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("kind", "message"),
    (
        ("digest", "digest does not match the promotion input"),
        ("commit", "source_commit does not match the promotion input"),
        ("run_commit", "head_sha does not match"),
        ("run_attempt", "run_attempt does not match"),
        ("workflow", "workflow_ref has an invalid format"),
    ),
)
def test_promotion_evidence_rejects_mismatched_identity(
    tmp_path: Path,
    kind: str,
    message: str,
) -> None:
    receipt = _promotion_receipt()
    run = _candidate_run()
    digest = DIGEST
    commit = COMMIT
    if kind == "digest":
        digest = REPLACEMENT
    elif kind == "commit":
        commit = "d" * 40
    elif kind == "run_commit":
        run["head_sha"] = "d" * 40
    elif kind == "run_attempt":
        run["run_attempt"] = 3
    else:
        receipt["workflow_ref"] = (
            "lbliii/furatena/.github/workflows/private-image.yml@refs/heads/feature"
        )

    completed = _verify_promotion_evidence(
        tmp_path,
        receipt=receipt,
        run=run,
        digest=digest,
        commit=commit,
    )

    assert completed.returncode != 0
    assert message in completed.stderr


def test_private_image_workflow_has_separate_candidate_and_lifecycle_authority() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(source)
    jobs = workflow["jobs"]
    triggers = workflow.get("on") or workflow.get(True)

    assert set(jobs) == {"pull-request", "candidate", "smoke", "lifecycle"}
    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["pull-request"]["permissions"] == {"contents": "read"}
    assert jobs["candidate"]["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
        "artifact-metadata": "write",
    }
    assert jobs["smoke"]["permissions"] == {"contents": "read", "packages": "read"}
    assert jobs["lifecycle"]["environment"] == "private-image-production"
    assert jobs["lifecycle"]["permissions"] == {
        "actions": "read",
        "contents": "write",
        "packages": "read",
    }
    assert "pypi" not in source.lower()
    assert "PYPI_TOKEN" not in source
    assert "pull_request:" in source
    assert "github.event.pull_request.head.repo.full_name == github.repository" in source
    pull_request = jobs["pull-request"]
    assert "docker/login-action" not in json.dumps(pull_request)
    assert "actions/attest" not in json.dumps(pull_request)
    expected_inputs = {
        ".dockerignore",
        ".github/schemas/private-image-promotion-evidence-v1.schema.json",
        ".github/workflows/private-image.yml",
        "Dockerfile",
        "LICENSE",
        "app/**",
        "config/**",
        "content/**",
        "pyproject.toml",
        "scripts/private_image_promotion_evidence.py",
        "scripts/private_image_release.py",
        "scripts/railway-start.sh",
        "scripts/verify-content-diagnostics.sh",
        "scripts/verify-unprivileged-image.sh",
        "src/**",
        "uv.lock",
    }
    assert set(triggers["push"]["paths"]) == expected_inputs
    assert set(triggers["pull_request"]["paths"]) == expected_inputs


def test_private_image_workflow_pins_supply_chain_actions_and_verifies_digest() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    pins = {
        "actions/checkout": "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
        "docker/login-action": "b45d80f862d83dbcd57f89517bcf500b2ab88fb2",
        "docker/setup-buildx-action": "4d04d5d9486b7bd6fa91e7baf45bbb4f8b9deedd",
        "docker/metadata-action": "030e881283bb7a6894de51c315a6bfe6a94e05cf",
        "docker/build-push-action": "f9f3042f7e2789586610d6e8b85c8f03e5195baf",
        "aquasecurity/trivy-action": "57a97c7e7821a5776cebc9bb87c984fa69cba8f1",
        "actions/upload-artifact": "bbbca2ddaa5d8feaa63e36b76fdaad77386f024f",
        "actions/attest": "c32b4b8b198b65d0bd9d63490e847ff7b53989d4",
    }
    for action, sha in pins.items():
        assert f"{action}@{sha}" in source
    assert "provenance: mode=max" in source
    assert "sbom: true" in source
    assert "push: true" in source
    assert "Build the pull-request image without publishing authority" in source
    assert "push: false" in source
    assert "load: true" in source
    assert "severity: CRITICAL,HIGH" in source
    assert "subject-digest: ${{ steps.build.outputs.digest }}" in source
    assert "push-to-registry: true" in source
    assert 'docker pull "$SUBJECT"' in source
    assert "docker buildx imagetools inspect" in source
    assert "gh attestation verify" in source
    assert "private-image-production" in source
    assert "options: [candidate, promote, deprecate, revoke]" in source
    assert "rollback_digest:" in source
    assert "compatibility:" in source
    assert "migration_notes:" in source
    assert "support_ends_at:" in source
    assert "candidate_run_id:" in source
    assert "Block promotion of a revoked digest" in source
    assert 'tag="image-revoked-${DIGEST#sha256:}"' in source
    assert "gh api graphql" in source
    assert "release(tagName: $tag)" in source
    assert "gh release list --limit" not in source
    assert "--rollback-digest" in source
    assert "--support-ends-at" in source
    assert "--clobber" not in source


def test_private_image_attestation_policy_is_operation_specific() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    promotion = source.split(
        "      - name: Verify exact candidate provenance before promotion\n", 1
    )[1].split("      - name: Verify existing provenance before deprecation\n", 1)[0]
    deprecation = source.split("      - name: Verify existing provenance before deprecation\n", 1)[
        1
    ].split("      - name: Create lifecycle record\n", 1)[0]

    assert "if: inputs.operation == 'promote'" in promotion
    assert "--signer-workflow" in promotion
    assert '--source-digest "$SOURCE_COMMIT"' in promotion
    assert "--source-ref refs/heads/main" in promotion
    assert "--deny-self-hosted-runners" in promotion

    assert "if: inputs.operation == 'deprecate'" in deprecation
    assert "--signer-workflow" in deprecation
    assert "--deny-self-hosted-runners" in deprecation
    assert "--source-digest" not in deprecation
    assert "--source-ref" not in deprecation


def test_promotion_requires_successful_exact_candidate_and_smoke_receipt() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    smoke = source.split("  smoke:\n", 1)[1].split("\n  lifecycle:\n", 1)[0]
    lifecycle = source.split("  lifecycle:\n", 1)[1]
    gate = lifecycle.split(
        "      - name: Require successful exact candidate and smoke evidence\n", 1
    )[1].split("      - name: Create lifecycle record\n", 1)[0]

    assert "Create exact candidate and smoke promotion evidence" in smoke
    assert "Prove unprivileged managed-content lifecycle" in smoke
    assert smoke.index("Prove unprivileged managed-content lifecycle") < smoke.index(
        "Create exact candidate and smoke promotion evidence"
    )
    assert "private-image-promotion-evidence-${{ github.sha }}" in smoke
    assert "if: inputs.operation == 'promote'" in gate
    assert 'gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${CANDIDATE_RUN_ID}"' in gate
    assert 'gh run download "$CANDIDATE_RUN_ID"' in gate
    assert "private_image_promotion_evidence.py verify" in gate
    assert '--digest "$DIGEST"' in gate
    assert '--commit "$COMMIT"' in gate


def test_private_image_smokes_reader_search_catalog_and_agent_surfaces() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    pull_request = source.split("  pull-request:\n", 1)[1].split("\n  candidate:\n", 1)[0]
    smoke = source.split("  smoke:\n", 1)[1].split("\n  lifecycle:\n", 1)[0]

    for job in (pull_request, smoke):
        for required in (
            "Accept: text/markdown",
            "/docs/get-started/",
            "/search/semantic?q=deployment",
            "/catalog/query.json",
            "/tools.json",
            "/llms.txt",
        ):
            assert required in job
        assert "test -s /tmp/furatena-reader.md" in job
        assert "test -s /tmp/furatena-search.json" in job
        assert "test -s /tmp/furatena-catalog.json" in job
        assert "test -s /tmp/furatena-tools.json" in job
        assert "test -s /tmp/furatena-llms.txt" in job


def test_release_runbook_covers_promotion_rollback_and_compromise() -> None:
    runbook = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8").lower()

    for required in (
        "digest",
        "candidate",
        "promote",
        "last-known-good",
        "rollback",
        "revoke",
        "do not delete",
        "never reuse",
        "registry credential",
        "private-image-production",
        "releases.atom",
        "image-record.json",
        "docker-image template",
        "same digest",
        "content volume",
    ):
        assert required in runbook
