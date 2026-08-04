"""Immutable publication artifact build, provenance, and readiness contracts."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from furatena.catalog.publication_artifacts import (
    GitPublicationArtifactCheckout,
    PublicationArtifactBuildResult,
    PublicationArtifactError,
    PublicationArtifactExecutor,
    PublicationArtifactRequest,
    PublicationArtifactService,
    exact_commit_from_outputs,
)
from furatena.catalog.publication_contracts import (
    PublicationOutputReference,
    PublicationRecordReference,
)
from furatena.catalog.publication_workflow import (
    PublicationExecutionDisposition,
    PublicationExecutionResult,
)
from tests.publication_support import digest, sample_actor, sample_plan

NOW = "2030-01-02T03:04:05Z"
FINGERPRINT_NAMES = (
    "content_ir",
    "frozen",
    "renderer",
    "theme",
    "catalog",
    "mount",
    "channel",
    "edition",
)


class DeterministicBuilder:
    def __init__(
        self,
        *,
        configuration: str = "config-v1",
        dependency_lock: str = "lock-v1",
        renderer: str = "renderer-v1",
        leak_private: bool = False,
        mutate_source: bool = False,
        attestation: str = "attestation:test",
    ) -> None:
        self.configuration = configuration
        self.dependency_lock = dependency_lock
        self.renderer = renderer
        self.leak_private = leak_private
        self.mutate_source = mutate_source
        self.attestation = attestation
        self.observed_source = ""

    def __call__(self, source_root: Path, output_root: Path, request: PublicationArtifactRequest):
        self.observed_source = (source_root / "docs" / "public.md").read_text(encoding="utf-8")
        if self.mutate_source:
            (source_root / "docs" / "builder-write.txt").write_text(
                "builder modified source", encoding="utf-8"
            )
        private = (source_root / "docs" / "private.md").read_text(encoding="utf-8")
        reader = self.observed_source
        if self.leak_private:
            reader += private
        outputs = {
            "index.html": reader,
            "search.json": json.dumps({"title": "Committed public guide"}),
            "catalog.json": json.dumps({"pages": ["index.html"]}),
            "llms.txt": "Committed public guide\n",
        }
        for name, value in outputs.items():
            (output_root / name).write_text(value, encoding="utf-8")
        fingerprint_values = {
            name: digest(self.renderer if name == "renderer" else f"{name}-v1")
            for name in FINGERPRINT_NAMES
        }
        return PublicationArtifactBuildResult(
            configuration_digest=digest(self.configuration),
            presentation_digest=digest("presentation-v1"),
            dependency_lock_digest=digest(self.dependency_lock),
            runtime_identity={"python_abi": "cp314t", "platform": "test-linux"},
            fingerprints=fingerprint_values,
            public_projection={
                "search": ("search.json",),
                "navigation": ("index.html",),
                "agent": ("llms.txt",),
                "export": ("catalog.json", "index.html"),
            },
            builder={"name": "test-freeze-export", "version": "1"},
            toolchain={"uv": "0.8", "furatena": "0.1.1"},
            build_command=("fura", "freeze", "&&", "fura", "export"),
            provenance_references=("validation:test",),
            attestation_references=(self.attestation,),
        )


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Artifact Test")
    _git(repository, "config", "user.email", "artifact@example.com")
    docs = repository / "docs"
    docs.mkdir()
    (docs / "public.md").write_text(
        "---\ntitle: Committed public guide\nvisibility: public\n---\nCommitted public body.\n",
        encoding="utf-8",
    )
    (docs / "private.md").write_text(
        "---\ntitle: Private roadmap canary\nvisibility: private\n---\n"
        "PRIVATE_ARTIFACT_CANARY_8M3L must never ship.\n",
        encoding="utf-8",
    )
    (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    _git(repository, "add", "docs", "uv.lock")
    _git(repository, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "source revision")
    return repository, _git(repository, "rev-parse", "HEAD").lower()


def _request(
    repository: Path,
    commit: str,
    *,
    suffix: str = "base",
    policy_version: str = "policy-v2",
) -> PublicationArtifactRequest:
    plan = sample_plan(
        correlation_id=f"artifact-{suffix}",
        idempotency_key=f"plan-{suffix}",
        policy_version=policy_version,
    )
    return PublicationArtifactRequest(
        plan=plan,
        workflow_revision_id=f"workflow-{suffix}",
        workflow_revision_digest=digest(f"workflow-{suffix}"),
        approval_records=(
            PublicationRecordReference(
                record_id=f"approval-{suffix}", record_digest=digest(f"approval-{suffix}")
            ),
        ),
        source_repository=str(repository),
        source_commit=commit,
        actor=sample_actor(),
        idempotency_key=f"artifact-{suffix}",
    )


def _service(root: Path, builder: DeterministicBuilder, *, now: str = NOW):
    return PublicationArtifactService(
        root,
        checkout=GitPublicationArtifactCheckout(),
        builder=builder,
        clock=lambda: now,
    )


def _manifest(service: PublicationArtifactService, artifact_id: str) -> dict[str, Any]:
    return json.loads(
        (service.artifacts / artifact_id / "manifest.json").read_text(encoding="utf-8")
    )


def test_build_uses_clean_exact_commit_and_exposes_verified_readiness(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    builder = DeterministicBuilder()
    service = _service(tmp_path / "store", builder)
    request = _request(repository, commit)
    (repository / "docs" / "public.md").write_text(
        "MUTABLE_AUTHOR_WORKSPACE_CONTENT\n", encoding="utf-8"
    )

    receipt = service.build(request)
    manifest = _manifest(service, receipt.artifact_id)
    verification = json.loads(
        (service.artifacts / receipt.artifact_id / "verification.json").read_text(encoding="utf-8")
    )
    current = json.loads(service.current.read_text(encoding="utf-8"))

    assert "Committed public body" in builder.observed_source
    assert "MUTABLE_AUTHOR_WORKSPACE_CONTENT" not in builder.observed_source
    assert manifest["source"] == {
        "repository": str(repository),
        "commit": commit,
        "tree_id": _git(repository, "rev-parse", "HEAD^{tree}").lower(),
        "checkout": {"isolated": True, "detached": True, "clean": True},
        "binding_revision": request.plan.bindings.source_revision,
    }
    assert manifest["dependency_lock_digest"] == digest("lock-v1")
    assert manifest["public_projection"]["privacy_scan"]["status"] == "pass"
    assert set(manifest["public_projection"]["inventory"]) == set(
        request.plan.impact.affected_projections
    )
    assert not (service.artifacts / receipt.artifact_id / "source").exists()
    assert service.status(receipt.artifact_id)["status"] == "verified"
    assert service.readiness(receipt.artifact_id)["status"] == "ready"
    assert service.readiness()["artifact_id"] == receipt.artifact_id

    schema_root = files("furatena.catalog").joinpath("schemas/publication-artifact/v1")
    for name, record in (
        ("manifest.schema.json", manifest),
        ("verification.schema.json", verification),
        ("current.schema.json", current),
    ):
        schema = json.loads(schema_root.joinpath(name).read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(record)


def test_rebuild_identity_excludes_time_and_optional_attestations(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    request = _request(repository, commit)
    first = _service(
        tmp_path / "first", DeterministicBuilder(attestation="attestation:first"), now=NOW
    )
    second = _service(
        tmp_path / "second",
        DeterministicBuilder(attestation="attestation:second"),
        now="2031-02-03T04:05:06Z",
    )

    first_receipt = first.build(request)
    second_receipt = second.build(request)

    assert first_receipt.artifact_id == second_receipt.artifact_id
    assert first_receipt.artifact_digest == second_receipt.artifact_digest
    assert first_receipt.manifest_digest != second_receipt.manifest_digest
    assert _manifest(first, first_receipt.artifact_id)["identity_exclusions"] == [
        "created_at",
        "provenance_references",
        "attestation_references",
    ]


def test_promotion_identity_is_full_verified_and_bound_to_exact_plan(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    service = _service(tmp_path / "store", DeterministicBuilder())
    request = _request(repository, commit)
    receipt = service.build(request)

    identity = service.promotion_identity(receipt.artifact_id)

    assert identity["artifact_id"] == receipt.artifact_id
    assert identity["artifact_digest"] == receipt.artifact_digest
    assert identity["manifest_digest"] == receipt.manifest_digest
    assert identity["plan_id"] == request.plan.plan_id
    assert identity["plan_digest"] == request.plan.plan_digest
    assert identity["policy_digest"] == request.plan.bindings.policy_digest
    assert identity["runtime_identity"] == {
        "platform": "test-linux",
        "python_abi": "cp314t",
    }
    assert set(identity["fingerprints"]) == set(FINGERPRINT_NAMES)
    assert str(identity["public_projection_digest"]).startswith("sha256:")

    manifest = service.artifacts / receipt.artifact_id / "manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["plan"]["plan_digest"] = digest("tampered-plan")
    manifest.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(PublicationArtifactError):
        service.promotion_identity(receipt.artifact_id)


def test_replay_repairs_an_interrupted_current_pointer_update(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    service = _service(tmp_path / "store", DeterministicBuilder())
    request = _request(repository, commit)
    first = service.build(request)
    service.current.unlink()

    replay = service.build(request)

    assert replay.replayed is True
    assert replay.artifact_id == first.artifact_id
    assert service.readiness()["artifact_id"] == first.artifact_id


def test_replay_never_rolls_back_a_newer_artifact_generation(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    root = tmp_path / "store"
    service = _service(root, DeterministicBuilder())
    first_request = _request(repository, commit)
    first = service.build(first_request)
    newer = service.build(_request(repository, commit, suffix="newer"))

    replay = service.build(first_request)

    current = json.loads(service.current.read_text(encoding="utf-8"))
    assert replay.replayed is True
    assert current["artifact_id"] == newer.artifact_id
    assert current["artifact_id"] != first.artifact_id
    assert current["generation"] == 2


@pytest.mark.parametrize(
    ("builder", "policy_version"),
    [
        (DeterministicBuilder(configuration="config-v2"), "policy-v2"),
        (DeterministicBuilder(dependency_lock="lock-v2"), "policy-v2"),
        (DeterministicBuilder(renderer="renderer-v2"), "policy-v2"),
        (DeterministicBuilder(), "policy-v3"),
    ],
)
def test_content_address_detects_configuration_dependency_renderer_and_policy_changes(
    tmp_path: Path, builder: DeterministicBuilder, policy_version: str
) -> None:
    repository, commit = _repository(tmp_path)
    baseline = _service(tmp_path / "baseline", DeterministicBuilder()).build(
        _request(repository, commit)
    )
    changed = _service(tmp_path / "changed", builder).build(
        _request(repository, commit, policy_version=policy_version)
    )

    assert changed.artifact_id != baseline.artifact_id


def test_content_address_detects_source_revision_change(tmp_path: Path) -> None:
    repository, first_commit = _repository(tmp_path)
    first = _service(tmp_path / "first", DeterministicBuilder()).build(
        _request(repository, first_commit)
    )
    (repository / "docs" / "public.md").write_text(
        "---\ntitle: Committed public guide\n---\nSecond committed body.\n", encoding="utf-8"
    )
    _git(repository, "add", "docs/public.md")
    _git(
        repository,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "-m",
        "second source revision",
    )
    second_commit = _git(repository, "rev-parse", "HEAD").lower()
    second = _service(tmp_path / "second", DeterministicBuilder()).build(
        _request(repository, second_commit, suffix="second")
    )

    assert second.artifact_id != first.artifact_id


@pytest.mark.parametrize(
    "field",
    ["source", "dependency_lock", "renderer", "policy", "configuration"],
)
def test_verification_detects_provenance_manifest_tampering(tmp_path: Path, field: str) -> None:
    repository, commit = _repository(tmp_path)
    service = _service(tmp_path / "store", DeterministicBuilder())
    receipt = service.build(_request(repository, commit))
    manifest_path = service.artifacts / receipt.artifact_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if field == "source":
        manifest["source"]["commit"] = "0" * 40
    elif field == "dependency_lock":
        manifest["dependency_lock_digest"] = digest("tampered-lock")
    elif field == "renderer":
        manifest["fingerprints"]["renderer"] = digest("tampered-renderer")
    elif field == "policy":
        manifest["policy"]["digest"] = digest("tampered-policy")
    else:
        manifest["configuration_digest"] = digest("tampered-config")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PublicationArtifactError) as failure:
        service.verify(receipt.artifact_id, full=False)

    assert failure.value.code == "artifact_corrupt"


@pytest.mark.parametrize("tamper", ["modify", "add"])
def test_full_verification_and_readiness_detect_artifact_tampering(
    tmp_path: Path, tamper: str
) -> None:
    repository, commit = _repository(tmp_path)
    service = _service(tmp_path / "store", DeterministicBuilder())
    receipt = service.build(_request(repository, commit))
    outputs = service.artifacts / receipt.artifact_id / "outputs"
    if tamper == "modify":
        (outputs / "index.html").write_text("tampered", encoding="utf-8")
    else:
        (outputs / "injected.txt").write_text("tampered", encoding="utf-8")

    with pytest.raises(PublicationArtifactError, match="immutable artifact"):
        service.verify(receipt.artifact_id, full=True)
    readiness = service.readiness(receipt.artifact_id)
    assert readiness["status"] == "not_ready"
    assert readiness["error_code"] == "artifact_corrupt"


def test_readiness_detects_current_pointer_tampering(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    service = _service(tmp_path / "store", DeterministicBuilder())
    service.build(_request(repository, commit))
    current = json.loads(service.current.read_text(encoding="utf-8"))
    current["manifest_digest"] = digest("tampered-current-pointer")
    service.current.write_text(json.dumps(current), encoding="utf-8")

    readiness = service.readiness()

    assert readiness["status"] == "not_ready"
    assert readiness["error_code"] == "current_artifact_corrupt"


def test_privacy_failure_never_replaces_last_complete_artifact(tmp_path: Path) -> None:
    repository, first_commit = _repository(tmp_path)
    root = tmp_path / "store"
    safe_service = _service(root, DeterministicBuilder())
    safe = safe_service.build(_request(repository, first_commit))
    before = {path.name for path in safe_service.artifacts.iterdir()}
    current_before = safe_service.current.read_bytes()
    (repository / "docs" / "public.md").write_text(
        "---\ntitle: Updated public guide\n---\nUpdated public body.\n", encoding="utf-8"
    )
    _git(repository, "add", "docs/public.md")
    _git(
        repository,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "-m",
        "updated revision",
    )
    second_commit = _git(repository, "rev-parse", "HEAD").lower()
    leaking_service = _service(root, DeterministicBuilder(leak_private=True))

    with pytest.raises(PublicationArtifactError) as failure:
        leaking_service.build(_request(repository, second_commit, suffix="leak"))

    assert failure.value.code == "privacy_scan_failed"
    assert {path.name for path in safe_service.artifacts.iterdir()} == before
    assert safe_service.current.read_bytes() == current_before
    assert safe_service.readiness(safe.artifact_id)["status"] == "ready"
    assert safe_service.readiness()["artifact_id"] == safe.artifact_id
    assert not list(leaking_service.staging.glob("build-*"))


def test_builder_cannot_modify_the_immutable_source_checkout(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    service = _service(tmp_path / "store", DeterministicBuilder(mutate_source=True))

    with pytest.raises(PublicationArtifactError) as failure:
        service.build(_request(repository, commit))

    assert failure.value.code == "source_checkout_dirty"
    assert not service.artifacts.exists()


def test_executor_builds_only_after_an_applied_exact_commit(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    service = _service(tmp_path / "store", DeterministicBuilder())
    plan = _request(repository, commit).plan

    class Delegate:
        def execute_with_context(self, plan, *, actor, idempotency_key):
            return PublicationExecutionResult(
                PublicationExecutionDisposition.APPLIED,
                (
                    PublicationOutputReference(
                        kind="publication_provider_result",
                        identifier="provider-result",
                        status="committed",
                        revision=commit,
                    ),
                ),
            )

        def reconcile_with_context(self, plan, *, actor):
            return None

    executor = PublicationArtifactExecutor(
        Delegate(),
        service=service,
        source_repository=str(repository),
        approval_resolver=lambda _plan: (
            PublicationRecordReference("approval-executor", digest("approval-executor")),
        ),
        source_commit_resolver=exact_commit_from_outputs,
    )

    result = executor.execute_with_context(
        plan, actor=sample_actor(), idempotency_key="executor-build"
    )

    artifact = next(output for output in result.outputs if output.kind == "publication_artifact")
    assert artifact.revision == commit
    assert artifact.status == "verified"
    assert service.readiness(artifact.identifier)["status"] == "ready"


def test_manifest_schema_rejects_contract_tampering(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    service = _service(tmp_path / "store", DeterministicBuilder())
    receipt = service.build(_request(repository, commit))
    record = _manifest(service, receipt.artifact_id)
    record["dependency_lock_digest"] = "mutable-lockfile"
    schema = json.loads(
        files("furatena.catalog")
        .joinpath("schemas/publication-artifact/v1/manifest.schema.json")
        .read_text(encoding="utf-8")
    )

    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(record)


def test_artifact_request_requires_every_bound_approval(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    request = _request(repository, commit)

    with pytest.raises(ValueError, match="every bound workflow approval"):
        replace(request, approval_records=())
