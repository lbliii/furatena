"""Private-image publication, promotion, and revocation contracts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "private_image_release.py"
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


def test_private_image_workflow_has_separate_candidate_and_lifecycle_authority() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(source)
    jobs = workflow["jobs"]
    triggers = workflow.get("on") or workflow.get(True)

    assert set(jobs) == {"candidate", "smoke", "lifecycle"}
    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["candidate"]["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
        "artifact-metadata": "write",
    }
    assert jobs["smoke"]["permissions"] == {"contents": "read", "packages": "read"}
    assert jobs["lifecycle"]["environment"] == "private-image-production"
    assert jobs["lifecycle"]["permissions"] == {"contents": "write", "packages": "read"}
    assert "pypi" not in source.lower()
    assert "PYPI_TOKEN" not in source
    assert "pull_request:" in source
    assert "github.event.pull_request.head.repo.full_name == github.repository" in source
    expected_inputs = {
        ".dockerignore",
        ".github/workflows/private-image.yml",
        "Dockerfile",
        "LICENSE",
        "app/**",
        "config/**",
        "content/**",
        "pyproject.toml",
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
    assert (
        "provenance: ${{ github.event_name == 'pull_request' && 'false' || 'mode=max' }}" in source
    )
    assert "sbom: ${{ github.event_name == 'pull_request' && 'false' || 'true' }}" in source
    assert "push: ${{ github.event_name != 'pull_request' }}" in source
    assert "load: ${{ github.event_name == 'pull_request' }}" in source
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
    assert "Block promotion of a revoked digest" in source
    assert 'tag="image-revoked-${DIGEST#sha256:}"' in source
    assert "--rollback-digest" in source
    assert "--support-ends-at" in source
    assert "--clobber" not in source


def test_private_image_smokes_reader_search_catalog_and_agent_surfaces() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    candidate = source.split("  candidate:\n", 1)[1].split("\n  smoke:\n", 1)[0]
    smoke = source.split("  smoke:\n", 1)[1].split("\n  lifecycle:\n", 1)[0]

    for job in (candidate, smoke):
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
