"""Content-addressed publication artifacts built from approved exact revisions."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

import yaml

from furatena.catalog.lifecycle import is_public_meta
from furatena.catalog.operation_lease import (
    OperationLease,
    operation_lease_seconds,
    operation_timeout_seconds,
)
from furatena.catalog.publication_contracts import (
    PublicationActor,
    PublicationFailure,
    PublicationFailureDisposition,
    PublicationOutputReference,
    PublicationPlan,
    PublicationRecordReference,
    canonical_json_bytes,
    normalize_rfc3339,
    sha256_digest,
)
from furatena.catalog.publication_workflow import (
    PublicationExecutionDisposition,
    PublicationExecutionError,
    PublicationExecutionResult,
)
from furatena.catalog.visibility_audit import VisibilityCanary, scan_visibility_leaks

PUBLICATION_ARTIFACT_SCHEMA_VERSION = 1
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ARTIFACT_ID = re.compile(r"^publication-artifact-[0-9a-f]{64}$")
_ARTIFACT_CONFLICT = "artifact_conflict"
_ARTIFACT_CORRUPT = "artifact_corrupt"
_ARTIFACT_EMPTY = "artifact_empty"
_ARTIFACT_NOT_FOUND = "artifact_not_found"
_ARTIFACT_UNSAFE = "artifact_unsafe"
_BUILD_FAILED = "build_failed"
_BUILD_IN_PROGRESS = "build_in_progress"
_CURRENT_ARTIFACT_CORRUPT = "current_artifact_corrupt"
_CURRENT_ARTIFACT_MISSING = "current_artifact_missing"
_IDEMPOTENCY_CONFLICT = "idempotency_conflict"
_PRIVACY_SCAN_FAILED = "privacy_scan_failed"
_SOURCE_CHECKOUT_CONFLICT = "source_checkout_conflict"
_SOURCE_CHECKOUT_DIRTY = "source_checkout_dirty"
_SOURCE_CHECKOUT_FAILED = "source_checkout_failed"
_SOURCE_CHECKOUT_INVALID = "source_checkout_invalid"
_SOURCE_REVISION_MISMATCH = "source_revision_mismatch"


class PublicationArtifactError(RuntimeError):
    """Sanitized deterministic artifact build or verification failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = _required(code, "error code")
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PublicationArtifactSource:
    repository: str
    resolved_commit: str
    tree_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", _required(self.repository, "source repository"))
        object.__setattr__(self, "resolved_commit", _commit(self.resolved_commit))
        object.__setattr__(self, "tree_id", _required(self.tree_id, "source tree ID"))


@dataclass(frozen=True, slots=True)
class PublicationArtifactRequest:
    plan: PublicationPlan
    workflow_revision_id: str
    workflow_revision_digest: str
    approval_records: tuple[PublicationRecordReference, ...]
    source_repository: str
    source_commit: str
    actor: PublicationActor
    idempotency_key: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "workflow_revision_id", _required(self.workflow_revision_id, "workflow revision")
        )
        object.__setattr__(self, "workflow_revision_digest", _digest(self.workflow_revision_digest))
        approvals = tuple(sorted(self.approval_records, key=lambda item: item.record_id))
        if len({item.record_id for item in approvals}) != len(approvals):
            raise ValueError(
                "Publication artifact approval references must be unique before building."
            )
        if len(approvals) < self.plan.approval_requirements.required_count:
            raise ValueError(
                "Publication artifacts require every bound workflow approval before building."
            )
        object.__setattr__(self, "approval_records", approvals)
        object.__setattr__(
            self, "source_repository", _required(self.source_repository, "source repository")
        )
        object.__setattr__(self, "source_commit", _commit(self.source_commit))
        object.__setattr__(
            self, "idempotency_key", _required(self.idempotency_key, "idempotency key")
        )

    @property
    def semantic_digest(self) -> str:
        return sha256_digest(
            canonical_json_bytes(
                {
                    "schema_version": PUBLICATION_ARTIFACT_SCHEMA_VERSION,
                    "plan_id": self.plan.plan_id,
                    "plan_digest": self.plan.plan_digest,
                    "workflow_revision_id": self.workflow_revision_id,
                    "workflow_revision_digest": self.workflow_revision_digest,
                    "approval_records": [item.to_dict() for item in self.approval_records],
                    "source_repository": self.source_repository,
                    "source_commit": self.source_commit,
                    "actor": self.actor.to_dict(),
                }
            )
        )


@dataclass(frozen=True, slots=True)
class PublicationArtifactBuildResult:
    configuration_digest: str
    presentation_digest: str
    dependency_lock_digest: str
    runtime_identity: Mapping[str, str]
    fingerprints: Mapping[str, str]
    public_projection: Mapping[str, Any]
    builder: Mapping[str, str]
    toolchain: Mapping[str, str]
    build_command: tuple[str, ...]
    provenance_references: tuple[str, ...] = ()
    attestation_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration_digest", _digest(self.configuration_digest))
        object.__setattr__(self, "presentation_digest", _digest(self.presentation_digest))
        object.__setattr__(self, "dependency_lock_digest", _digest(self.dependency_lock_digest))
        for name in ("runtime_identity", "builder", "toolchain"):
            value = _string_mapping(getattr(self, name), name)
            if not value:
                raise ValueError(
                    f"Publication artifact {name} is required to identify the approved build."
                )
            object.__setattr__(self, name, value)
        fingerprints = _string_mapping(self.fingerprints, "fingerprints")
        required = {
            "content_ir",
            "frozen",
            "renderer",
            "theme",
            "catalog",
            "mount",
            "channel",
            "edition",
        }
        if set(fingerprints) != required:
            raise ValueError(
                "Publication artifact fingerprints must identify every required output surface."
            )
        fingerprints = {name: _digest(value) for name, value in fingerprints.items()}
        object.__setattr__(self, "fingerprints", fingerprints)
        projection = dict(self.public_projection)
        canonical_json_bytes(projection)
        object.__setattr__(self, "public_projection", projection)
        command = tuple(_required(item, "build command item") for item in self.build_command)
        if not command:
            raise ValueError(
                "Publication artifact build commands must identify the invoked builder operation."
            )
        object.__setattr__(self, "build_command", command)
        for name in ("provenance_references", "attestation_references"):
            object.__setattr__(
                self,
                name,
                tuple(sorted({_required(item, name) for item in getattr(self, name)})),
            )


@dataclass(frozen=True, slots=True)
class PublicationArtifactReceipt:
    request_digest: str
    artifact_id: str
    artifact_digest: str
    manifest_digest: str
    source_commit: str
    generation: int
    state: str
    replayed: bool
    created_at: str

    def __post_init__(self) -> None:
        for name in ("request_digest", "artifact_digest", "manifest_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name)))
        if _ARTIFACT_ID.fullmatch(self.artifact_id) is None:
            raise ValueError(
                "Publication artifact IDs must contain the verified SHA-256 content address."
            )
        object.__setattr__(self, "source_commit", _commit(self.source_commit))
        if self.generation < 1:
            raise ValueError(
                "Publication artifact receipt generations must be positive monotonic integers."
            )
        if self.state != "verified":
            raise ValueError(
                "Publication artifact receipts must reference output in the verified state."
            )
        object.__setattr__(self, "created_at", normalize_rfc3339(self.created_at))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PUBLICATION_ARTIFACT_SCHEMA_VERSION,
            "record_type": "furatena.publication.artifact-receipt",
            "request_digest": self.request_digest,
            "artifact_id": self.artifact_id,
            "artifact_digest": self.artifact_digest,
            "manifest_digest": self.manifest_digest,
            "source_commit": self.source_commit,
            "generation": self.generation,
            "state": self.state,
            "replayed": self.replayed,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationArtifactReceipt:
        if value.get("schema_version") != PUBLICATION_ARTIFACT_SCHEMA_VERSION:
            raise ValueError(
                "Publication artifact receipts must use the supported schema version 1."
            )
        if value.get("record_type") != "furatena.publication.artifact-receipt":
            raise ValueError(
                "Publication artifact receipts must use the expected artifact receipt type."
            )
        return cls(
            request_digest=str(value.get("request_digest") or ""),
            artifact_id=str(value.get("artifact_id") or ""),
            artifact_digest=str(value.get("artifact_digest") or ""),
            manifest_digest=str(value.get("manifest_digest") or ""),
            source_commit=str(value.get("source_commit") or ""),
            generation=int(value.get("generation") or 0),
            state=str(value.get("state") or ""),
            replayed=bool(value.get("replayed")),
            created_at=str(value.get("created_at") or ""),
        )

    def output_reference(self) -> PublicationOutputReference:
        return PublicationOutputReference(
            kind="publication_artifact",
            identifier=self.artifact_id,
            status=self.state,
            revision=self.source_commit,
        )


class PublicationArtifactCheckout(Protocol):
    def __call__(
        self, repository: str, commit: str, target: Path, /
    ) -> PublicationArtifactSource: ...


class GitPublicationArtifactCheckout:
    """Create a detached, independent checkout of one exact Git commit."""

    def __call__(self, repository: str, commit: str, target: Path, /) -> PublicationArtifactSource:
        repository = _required(repository, "source repository")
        commit = _commit(commit)
        if target.exists():
            raise PublicationArtifactError(
                _SOURCE_CHECKOUT_CONFLICT,
                "The isolated publication checkout target already exists.",
            )
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _run_git(
            ("clone", "--no-checkout", "--no-local", "--quiet", "--", repository, str(target)),
            cwd=target.parent,
            code=_SOURCE_CHECKOUT_FAILED,
            message="The approved repository could not be cloned into an isolated checkout.",
        )
        _run_git(
            ("checkout", "--detach", "--quiet", commit),
            cwd=target,
            code=_SOURCE_CHECKOUT_FAILED,
            message="The approved exact commit could not be checked out.",
        )
        resolved = _git_output(("rev-parse", "HEAD"), cwd=target)
        tree_id = _git_output(("rev-parse", "HEAD^{tree}"), cwd=target)
        _verify_clean_checkout(target, commit)
        return PublicationArtifactSource(repository, resolved, tree_id)


class PublicationArtifactBuilder(Protocol):
    def __call__(
        self, source_root: Path, output_root: Path, request: PublicationArtifactRequest, /
    ) -> PublicationArtifactBuildResult: ...


class PublicationArtifactService:
    """Build-once store for approved publication artifacts."""

    def __init__(
        self,
        root: Path,
        *,
        checkout: PublicationArtifactCheckout,
        builder: PublicationArtifactBuilder,
        clock: Callable[[], str],
    ) -> None:
        self.root = root.resolve()
        self.artifacts = self.root / "artifacts"
        self.operations = self.root / "operations"
        self.requests = self.root / "requests"
        self.staging = self.root / "staging"
        self.leases = self.root / "leases"
        self.current = self.root / "current.json"
        self.checkout = checkout
        self.builder = builder
        self.clock = clock
        self._lock = threading.RLock()

    def build(self, request: PublicationArtifactRequest) -> PublicationArtifactReceipt:
        request_digest = request.semantic_digest
        operation_path = self.operations / f"{_key_digest(request.idempotency_key)}.json"
        with self._lock, self._lease():
            existing_operation = _read_json(operation_path)
            if existing_operation is not None:
                if existing_operation.get("request_digest") != request_digest:
                    raise PublicationArtifactError(
                        _IDEMPOTENCY_CONFLICT,
                        "The artifact idempotency key is bound to a different approved revision.",
                    )
                if existing_operation.get("status") == "succeeded":
                    receipt = self._receipt_for_request(request_digest)
                    self.verify(receipt.artifact_id, full=True)
                    self._promote_current(receipt)
                    return replace(receipt, replayed=True)
                if existing_operation.get("status") == "building":
                    raise PublicationArtifactError(
                        _BUILD_IN_PROGRESS,
                        "The approved artifact build is already in progress.",
                    )
            indexed = self._indexed_receipt(request_digest)
            if indexed is not None:
                self.verify(indexed.artifact_id, full=True)
                _write_json_replace(
                    operation_path,
                    self._operation(request, status="succeeded", artifact_id=indexed.artifact_id),
                )
                self._promote_current(indexed)
                return replace(indexed, replayed=True)
            _write_json_replace(operation_path, self._operation(request, status="building"))
            stage = self.staging / f"build-{uuid.uuid4().hex}"
            source_root = stage / "source"
            output_root = stage / "outputs"
            try:
                source_root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = self.checkout(
                    request.source_repository, request.source_commit, source_root
                )
                if (
                    source.repository != request.source_repository
                    or source.resolved_commit != request.source_commit
                ):
                    raise PublicationArtifactError(
                        _SOURCE_REVISION_MISMATCH,
                        "The isolated checkout did not resolve to the approved repository and commit.",
                    )
                _verify_clean_checkout(source_root, request.source_commit)
                output_root.mkdir(mode=0o700)
                build = self.builder(source_root, output_root, request)
                _verify_clean_checkout(source_root, request.source_commit)
                projection = _verify_public_projection(source_root, output_root)
                inventory = _inventory(output_root)
                projection_inventory = _projection_inventory(
                    build.public_projection,
                    inventory,
                    request.plan.impact.affected_projections,
                )
                shutil.rmtree(source_root)
                created_at = normalize_rfc3339(self.clock())
                manifest = self._manifest(
                    request,
                    source,
                    build,
                    inventory,
                    projection_inventory,
                    projection,
                    created_at,
                )
                artifact_digest = sha256_digest(canonical_json_bytes(_identity_payload(manifest)))
                artifact_id = f"publication-artifact-{artifact_digest.removeprefix('sha256:')}"
                manifest["artifact_id"] = artifact_id
                manifest["artifact_digest"] = artifact_digest
                manifest_path = stage / "manifest.json"
                _write_json_replace(manifest_path, manifest)
                verification = {
                    "schema_version": PUBLICATION_ARTIFACT_SCHEMA_VERSION,
                    "record_type": "furatena.publication.artifact-verification",
                    "artifact_id": artifact_id,
                    "artifact_digest": artifact_digest,
                    "manifest_digest": _file_digest(manifest_path),
                    "status": "verified",
                    "artifact_count": len(inventory),
                    "artifact_bytes": _inventory_bytes(inventory),
                    "privacy_scan": projection,
                    "verified_at": created_at,
                }
                _write_json_replace(stage / "verification.json", verification)
                self._verify_root(stage, full=True, expected_id=artifact_id)
                final = self.artifacts / artifact_id
                self.artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
                if final.exists():
                    self._verify_root(final, full=True, expected_id=artifact_id)
                    shutil.rmtree(stage, ignore_errors=True)
                else:
                    os.replace(stage, final)
                stored_verification = self._verify_root(final, full=True, expected_id=artifact_id)
                stored_manifest = _read_json(final / "manifest.json")
                if stored_manifest is None:
                    raise PublicationArtifactError(
                        _ARTIFACT_CORRUPT,
                        "The promoted immutable artifact manifest cannot be read.",
                    )
                receipt = PublicationArtifactReceipt(
                    request_digest=request_digest,
                    artifact_id=artifact_id,
                    artifact_digest=artifact_digest,
                    manifest_digest=str(stored_verification["manifest_digest"]),
                    source_commit=request.source_commit,
                    generation=self._next_generation(),
                    state="verified",
                    replayed=False,
                    created_at=str(stored_manifest["created_at"]),
                )
                _write_json_once(
                    self.requests / f"{request_digest.removeprefix('sha256:')}.json",
                    receipt.to_dict(),
                )
                _write_json_replace(
                    operation_path,
                    self._operation(request, status="succeeded", artifact_id=artifact_id),
                )
                self._promote_current(receipt)
                return receipt
            except PublicationArtifactError as exc:
                _write_json_replace(
                    operation_path,
                    self._operation(request, status="failed", error_code=exc.code),
                )
                raise
            except Exception as exc:
                _write_json_replace(
                    operation_path,
                    self._operation(request, status="failed", error_code=_BUILD_FAILED),
                )
                raise PublicationArtifactError(
                    _BUILD_FAILED,
                    "The approved artifact build failed before completion.",
                ) from exc
            finally:
                shutil.rmtree(stage, ignore_errors=True)

    def verify(self, artifact_id: str, *, full: bool) -> dict[str, object]:
        if _ARTIFACT_ID.fullmatch(artifact_id) is None:
            raise PublicationArtifactError(
                _ARTIFACT_NOT_FOUND,
                "The artifact identifier is invalid and cannot be verified.",
            )
        return self._verify_root(self.artifacts / artifact_id, full=full, expected_id=artifact_id)

    def status(self, artifact_id: str) -> dict[str, object]:
        verification = self.verify(artifact_id, full=False)
        return {
            "schema_version": PUBLICATION_ARTIFACT_SCHEMA_VERSION,
            "artifact_id": verification["artifact_id"],
            "artifact_digest": verification["artifact_digest"],
            "manifest_digest": verification["manifest_digest"],
            "status": verification["status"],
            "artifact_count": verification["artifact_count"],
            "artifact_bytes": verification["artifact_bytes"],
        }

    def promotion_identity(self, artifact_id: str) -> dict[str, object]:
        """Return the fully verified identity required by environment promotion."""
        verification = self.verify(artifact_id, full=True)
        manifest = _read_json(self.artifacts / artifact_id / "manifest.json")
        if manifest is None:
            raise PublicationArtifactError(
                _ARTIFACT_CORRUPT,
                "The immutable artifact manifest cannot be read for promotion.",
            )
        plan = manifest.get("plan")
        policy = manifest.get("policy")
        runtime = manifest.get("runtime_identity")
        projection = manifest.get("public_projection")
        fingerprints = manifest.get("fingerprints")
        if (
            not isinstance(plan, Mapping)
            or not isinstance(policy, Mapping)
            or not isinstance(runtime, Mapping)
            or not isinstance(projection, Mapping)
            or not isinstance(fingerprints, Mapping)
        ):
            raise PublicationArtifactError(
                _ARTIFACT_CORRUPT,
                "The immutable artifact lacks a complete promotion identity.",
            )
        return {
            "schema_version": PUBLICATION_ARTIFACT_SCHEMA_VERSION,
            "artifact_id": artifact_id,
            "artifact_digest": verification["artifact_digest"],
            "manifest_digest": verification["manifest_digest"],
            "plan_id": str(plan.get("plan_id") or ""),
            "plan_digest": str(plan.get("plan_digest") or ""),
            "policy_version": str(policy.get("version") or ""),
            "policy_digest": str(policy.get("digest") or ""),
            "configuration_digest": str(manifest.get("configuration_digest") or ""),
            "presentation_digest": str(manifest.get("presentation_digest") or ""),
            "runtime_identity": dict(runtime),
            "fingerprints": dict(fingerprints),
            "public_projection_digest": sha256_digest(canonical_json_bytes(projection)),
        }

    def readiness(self, artifact_id: str | None = None) -> dict[str, object]:
        """Return a fail-closed readiness projection after full artifact verification."""
        resolved_id = artifact_id
        current: Mapping[str, Any] | None = None
        try:
            if resolved_id is None:
                current = _read_json(self.current)
                if (
                    current is None
                    or current.get("schema_version") != PUBLICATION_ARTIFACT_SCHEMA_VERSION
                    or current.get("record_type") != "furatena.publication.current-artifact"
                ):
                    raise PublicationArtifactError(
                        _CURRENT_ARTIFACT_MISSING,
                        "The current verified publication artifact is unavailable.",
                    )
                resolved_id = str(current.get("artifact_id") or "")
            verification = self.verify(resolved_id, full=True)
            if current is not None and (
                current.get("artifact_digest") != verification["artifact_digest"]
                or current.get("manifest_digest") != verification["manifest_digest"]
            ):
                raise PublicationArtifactError(
                    _CURRENT_ARTIFACT_CORRUPT,
                    "The current publication artifact pointer does not match its manifest.",
                )
        except PublicationArtifactError as exc:
            return {
                "schema_version": PUBLICATION_ARTIFACT_SCHEMA_VERSION,
                "kind": "publication_artifact_readiness",
                "artifact_id": resolved_id or None,
                "ok": False,
                "status": "not_ready",
                "error_code": exc.code,
                "remediation": "Rebuild the artifact from its approved exact revision.",
            }
        return {
            "schema_version": PUBLICATION_ARTIFACT_SCHEMA_VERSION,
            "kind": "publication_artifact_readiness",
            "artifact_id": resolved_id,
            "artifact_digest": verification["artifact_digest"],
            "manifest_digest": verification["manifest_digest"],
            "ok": True,
            "status": "ready",
            "artifact_count": verification["artifact_count"],
            "artifact_bytes": verification["artifact_bytes"],
            "error_code": None,
            "remediation": None,
        }

    def reconcile(self) -> dict[str, int]:
        """Locally fail interrupted operations and remove incomplete staging trees."""
        interrupted = 0
        removed = 0
        with self._lock, self._lease():
            for path in sorted(self.operations.glob("*.json")) if self.operations.is_dir() else ():
                operation = _read_json(path)
                if operation is not None and operation.get("status") == "building":
                    operation["status"] = "failed"
                    operation["error_code"] = "worker_interrupted"
                    operation["updated_at"] = normalize_rfc3339(self.clock())
                    _write_json_replace(path, operation)
                    interrupted += 1
            for path in sorted(self.staging.glob("build-*")) if self.staging.is_dir() else ():
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                    removed += 1
        return {"interrupted_operations": interrupted, "removed_staging": removed}

    def _manifest(
        self,
        request: PublicationArtifactRequest,
        source: PublicationArtifactSource,
        build: PublicationArtifactBuildResult,
        inventory: list[dict[str, object]],
        projection_inventory: Mapping[str, Sequence[str]],
        projection: Mapping[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": PUBLICATION_ARTIFACT_SCHEMA_VERSION,
            "record_type": "furatena.publication.artifact-manifest",
            "artifact_id": None,
            "artifact_digest": None,
            "plan": {"plan_id": request.plan.plan_id, "plan_digest": request.plan.plan_digest},
            "workflow": {
                "revision_id": request.workflow_revision_id,
                "revision_digest": request.workflow_revision_digest,
            },
            "approvals": [item.to_dict() for item in request.approval_records],
            "source": {
                "repository": source.repository,
                "commit": source.resolved_commit,
                "tree_id": source.tree_id,
                "checkout": {"isolated": True, "detached": True, "clean": True},
                "binding_revision": request.plan.bindings.source_revision,
            },
            "validation": {
                "run_id": request.plan.validation.run_id,
                "snapshot_id": request.plan.validation.snapshot_id,
                "diagnostics_digest": request.plan.validation.diagnostics_digest,
            },
            "policy": {
                "version": request.plan.bindings.policy_version,
                "digest": request.plan.bindings.policy_digest,
            },
            "configuration_digest": build.configuration_digest,
            "presentation_digest": build.presentation_digest,
            "dependency_lock_digest": build.dependency_lock_digest,
            "runtime_identity": dict(build.runtime_identity),
            "fingerprints": dict(build.fingerprints),
            "inventory": inventory,
            "public_projection": {
                "inventory": {
                    name: list(paths) for name, paths in sorted(projection_inventory.items())
                },
                "privacy_scan": dict(projection),
            },
            "builder": dict(build.builder),
            "toolchain": {
                **dict(build.toolchain),
                "python": sys.version.split()[0],
                "implementation": sys.implementation.name,
            },
            "build_command": list(build.build_command),
            "provenance_references": list(build.provenance_references),
            "attestation_references": list(build.attestation_references),
            "actor": request.actor.to_dict(),
            "correlation_id": request.plan.correlation_id,
            "created_at": created_at,
            "identity_exclusions": [
                "created_at",
                "provenance_references",
                "attestation_references",
            ],
        }

    def _verify_root(self, root: Path, *, full: bool, expected_id: str) -> dict[str, object]:
        manifest = _read_json(root / "manifest.json")
        verification = _read_json(root / "verification.json")
        if manifest is None or verification is None:
            raise PublicationArtifactError(
                _ARTIFACT_CORRUPT,
                "The immutable artifact manifest is missing or invalid.",
            )
        if (
            manifest.get("schema_version") != PUBLICATION_ARTIFACT_SCHEMA_VERSION
            or manifest.get("record_type") != "furatena.publication.artifact-manifest"
            or verification.get("record_type") != "furatena.publication.artifact-verification"
            or manifest.get("artifact_id") != expected_id
            or verification.get("artifact_id") != expected_id
            or verification.get("status") != "verified"
            or verification.get("manifest_digest") != _file_digest(root / "manifest.json")
        ):
            raise PublicationArtifactError(
                _ARTIFACT_CORRUPT,
                "The immutable artifact verification record does not match.",
            )
        artifact_digest = sha256_digest(canonical_json_bytes(_identity_payload(manifest)))
        if (
            manifest.get("artifact_digest") != artifact_digest
            or verification.get("artifact_digest") != artifact_digest
            or expected_id != f"publication-artifact-{artifact_digest.removeprefix('sha256:')}"
        ):
            raise PublicationArtifactError(
                _ARTIFACT_CORRUPT,
                "The immutable artifact content address does not match.",
            )
        inventory = manifest.get("inventory")
        if not isinstance(inventory, list) or not inventory:
            raise PublicationArtifactError(
                _ARTIFACT_CORRUPT,
                "The artifact inventory is empty and cannot be verified.",
            )
        if verification.get("artifact_count") != len(inventory) or verification.get(
            "artifact_bytes"
        ) != sum(int(item.get("bytes") or 0) for item in inventory if isinstance(item, Mapping)):
            raise PublicationArtifactError(
                _ARTIFACT_CORRUPT,
                "The artifact inventory summary does not match its verification record.",
            )
        if full:
            _verify_inventory(root / "outputs", inventory)
        _verify_manifest_projection(manifest, inventory)
        return {
            "artifact_id": expected_id,
            "artifact_digest": artifact_digest,
            "manifest_digest": str(verification["manifest_digest"]),
            "status": "verified",
            "artifact_count": len(inventory),
            "artifact_bytes": int(verification["artifact_bytes"]),
        }

    def _operation(
        self,
        request: PublicationArtifactRequest,
        *,
        status: str,
        artifact_id: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, object]:
        return {
            "schema_version": PUBLICATION_ARTIFACT_SCHEMA_VERSION,
            "record_type": "furatena.publication.artifact-operation",
            "idempotency_key_digest": f"sha256:{_key_digest(request.idempotency_key)}",
            "request_digest": request.semantic_digest,
            "plan_id": request.plan.plan_id,
            "status": status,
            "artifact_id": artifact_id,
            "error_code": error_code,
            "updated_at": normalize_rfc3339(self.clock()),
        }

    def _indexed_receipt(self, request_digest: str) -> PublicationArtifactReceipt | None:
        value = _read_json(self.requests / f"{request_digest.removeprefix('sha256:')}.json")
        return PublicationArtifactReceipt.from_dict(value) if value is not None else None

    def _receipt_for_request(self, request_digest: str) -> PublicationArtifactReceipt:
        receipt = self._indexed_receipt(request_digest)
        if receipt is None:
            raise PublicationArtifactError(
                _ARTIFACT_CORRUPT,
                "The completed artifact request index is missing from durable storage.",
            )
        return receipt

    def _promote_current(self, receipt: PublicationArtifactReceipt) -> None:
        current = _read_json(self.current)
        if current is not None:
            current_generation = current.get("generation")
            if not isinstance(current_generation, int) or current_generation < 1:
                raise PublicationArtifactError(
                    _CURRENT_ARTIFACT_CORRUPT,
                    "The current publication artifact pointer has an invalid generation.",
                )
            if current_generation >= receipt.generation:
                return
        _write_json_replace(
            self.current,
            {
                "schema_version": PUBLICATION_ARTIFACT_SCHEMA_VERSION,
                "record_type": "furatena.publication.current-artifact",
                "artifact_id": receipt.artifact_id,
                "artifact_digest": receipt.artifact_digest,
                "manifest_digest": receipt.manifest_digest,
                "source_commit": receipt.source_commit,
                "generation": receipt.generation,
                "updated_at": receipt.created_at,
            },
        )

    def _next_generation(self) -> int:
        generations: list[int] = []
        current = _read_json(self.current)
        if current is not None:
            value = current.get("generation")
            if not isinstance(value, int) or value < 1:
                raise PublicationArtifactError(
                    _CURRENT_ARTIFACT_CORRUPT,
                    "The current publication artifact pointer has an invalid generation.",
                )
            generations.append(value)
        if self.requests.is_dir():
            for path in sorted(self.requests.glob("*.json")):
                value = _read_json(path)
                if value is None:
                    raise PublicationArtifactError(
                        _ARTIFACT_CORRUPT,
                        "A publication artifact receipt cannot be read during generation allocation.",
                    )
                generations.append(PublicationArtifactReceipt.from_dict(value).generation)
        return max(generations, default=0) + 1

    def _lease(self) -> OperationLease:
        return OperationLease(
            self.leases,
            "publication-artifact-build",
            resource=str(self.root),
            timeout_seconds=operation_timeout_seconds(),
            lease_seconds=operation_lease_seconds(),
        )


type ApprovalResolver = Callable[[PublicationPlan], tuple[PublicationRecordReference, ...]]
type SourceCommitResolver = Callable[[PublicationPlan, tuple[PublicationOutputReference, ...]], str]


class PublicationArtifactExecutor:
    """Contextual workflow executor that builds once after an applied revision."""

    def __init__(
        self,
        delegate: Any,
        *,
        service: PublicationArtifactService,
        source_repository: str,
        approval_resolver: ApprovalResolver,
        source_commit_resolver: SourceCommitResolver,
    ) -> None:
        self.delegate = delegate
        self.service = service
        self.source_repository = _required(source_repository, "source repository")
        self.approval_resolver = approval_resolver
        self.source_commit_resolver = source_commit_resolver

    def execute(self, plan: PublicationPlan, /) -> PublicationExecutionResult:
        return self.execute_with_context(
            plan, actor=plan.creator, idempotency_key=plan.idempotency_key
        )

    def execute_with_context(
        self,
        plan: PublicationPlan,
        /,
        *,
        actor: PublicationActor,
        idempotency_key: str,
    ) -> PublicationExecutionResult:
        contextual = getattr(self.delegate, "execute_with_context", None)
        raw = (
            contextual(plan, actor=actor, idempotency_key=idempotency_key)
            if callable(contextual)
            else self.delegate.execute(plan)
        )
        result = _execution_result(raw)
        return self._build_if_applied(plan, result, actor, idempotency_key)

    def reconcile(self, plan: PublicationPlan, /) -> PublicationExecutionResult | None:
        return self.reconcile_with_context(plan, actor=plan.creator)

    def reconcile_with_context(
        self, plan: PublicationPlan, /, *, actor: PublicationActor
    ) -> PublicationExecutionResult | None:
        contextual = getattr(self.delegate, "reconcile_with_context", None)
        raw = (
            contextual(plan, actor=actor) if callable(contextual) else self.delegate.reconcile(plan)
        )
        if raw is None:
            return None
        result = _execution_result(raw)
        return self._build_if_applied(plan, result, actor, plan.idempotency_key)

    def _build_if_applied(
        self,
        plan: PublicationPlan,
        result: PublicationExecutionResult,
        actor: PublicationActor,
        idempotency_key: str,
    ) -> PublicationExecutionResult:
        if result.disposition != PublicationExecutionDisposition.APPLIED:
            return result
        try:
            commit = _commit(self.source_commit_resolver(plan, result.outputs))
            approvals = self.approval_resolver(plan)
            workflow_digest = sha256_digest(
                canonical_json_bytes(
                    {
                        "plan_id": plan.plan_id,
                        "plan_digest": plan.plan_digest,
                        "idempotency_key": idempotency_key,
                        "source_commit": commit,
                    }
                )
            )
            request = PublicationArtifactRequest(
                plan=plan,
                workflow_revision_id=f"workflow-execution-{workflow_digest.removeprefix('sha256:')[:24]}",
                workflow_revision_digest=workflow_digest,
                approval_records=approvals,
                source_repository=self.source_repository,
                source_commit=commit,
                actor=actor,
                idempotency_key=f"artifact:{idempotency_key}",
            )
            receipt = self.service.build(request)
        except (PublicationArtifactError, ValueError) as exc:
            code = getattr(exc, "code", "artifact.authorization_invalid")
            raise PublicationExecutionError(
                PublicationFailure(
                    PublicationFailureDisposition.TERMINAL,
                    f"artifact.{code}",
                    "The approved publication artifact could not be built or verified.",
                    "Inspect the sanitized artifact operation and create a fresh approved revision.",
                ),
                outputs=result.outputs,
            ) from exc
        return PublicationExecutionResult(
            PublicationExecutionDisposition.APPLIED,
            (*result.outputs, receipt.output_reference()),
        )


def exact_commit_from_outputs(
    _plan: PublicationPlan, outputs: tuple[PublicationOutputReference, ...]
) -> str:
    """Resolve the exact applied provider commit without accepting mutable refs."""
    for output in reversed(outputs):
        revision = str(output.revision or "").lower()
        if _COMMIT.fullmatch(revision):
            return revision
    raise ValueError(
        "Applied publication outputs must contain an exact commit before artifact building."
    )


def _execution_result(value: Any) -> PublicationExecutionResult:
    if isinstance(value, PublicationExecutionResult):
        return value
    if isinstance(value, tuple) and all(
        isinstance(item, PublicationOutputReference) for item in value
    ):
        return PublicationExecutionResult(PublicationExecutionDisposition.APPLIED, value)
    raise TypeError("publication executor returned an unsupported artifact input")


def _identity_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    excluded = {
        "artifact_id",
        "artifact_digest",
        "created_at",
        "provenance_references",
        "attestation_references",
    }
    return {key: value for key, value in manifest.items() if key not in excluded}


def _inventory(root: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PublicationArtifactError(
                _ARTIFACT_UNSAFE,
                "Publication artifacts cannot contain symbolic links.",
            )
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        media_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        records.append(
            {
                "path": relative,
                "media_type": media_type,
                "bytes": path.stat().st_size,
                "sha256": _file_digest(path),
            }
        )
    if not records:
        raise PublicationArtifactError(
            _ARTIFACT_EMPTY,
            "The publication artifact is empty and cannot be promoted.",
        )
    return records


def _inventory_bytes(inventory: Sequence[Mapping[str, object]]) -> int:
    total = 0
    for item in inventory:
        value = item.get("bytes")
        if not isinstance(value, int):
            raise PublicationArtifactError(
                _ARTIFACT_CORRUPT,
                "The artifact inventory contains an invalid file size value.",
            )
        total += value
    return total


def _verify_inventory(root: Path, inventory: Sequence[Any]) -> None:
    seen: set[str] = set()
    for raw in inventory:
        if not isinstance(raw, Mapping):
            raise PublicationArtifactError(
                _ARTIFACT_CORRUPT,
                "The artifact inventory entry is invalid and cannot be verified.",
            )
        relative = Path(str(raw.get("path") or ""))
        name = relative.as_posix()
        expected_bytes = raw.get("bytes")
        path = root / relative
        if (
            not name
            or relative.is_absolute()
            or ".." in relative.parts
            or name in seen
            or not isinstance(expected_bytes, int)
            or not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != expected_bytes
            or _file_digest(path) != raw.get("sha256")
        ):
            raise PublicationArtifactError(
                _ARTIFACT_CORRUPT,
                "An immutable artifact failed size or SHA-256 verification.",
            )
        seen.add(name)
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual != seen:
        raise PublicationArtifactError(
            _ARTIFACT_CORRUPT,
            "The immutable artifact contains files outside its manifest inventory.",
        )


def _projection_inventory(
    value: Mapping[str, Any],
    inventory: Sequence[Mapping[str, object]],
    required_projections: Sequence[str],
) -> dict[str, tuple[str, ...]]:
    artifact_paths = {str(item.get("path") or "") for item in inventory}
    normalized: dict[str, tuple[str, ...]] = {}
    for raw_name, raw_paths in value.items():
        name = _required(raw_name, "public projection name")
        if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, str):
            raise ValueError(
                "Publication artifact projection inventories must contain generated artifact path lists."
            )
        paths = tuple(sorted({_required(path, f"{name} projection path") for path in raw_paths}))
        if not paths or any(path not in artifact_paths for path in paths):
            raise ValueError(
                "Publication artifact projection inventories must reference existing generated artifacts."
            )
        normalized[name] = paths
    required = {_required(name, "affected projection") for name in required_projections}
    missing = required - set(normalized)
    if missing:
        raise ValueError(
            "Publication artifact projection inventory is missing affected public projections: "
            + ", ".join(sorted(missing))
            + "."
        )
    if not normalized:
        raise ValueError(
            "Publication artifacts require at least one public projection inventory before promotion."
        )
    return dict(sorted(normalized.items()))


def _verify_manifest_projection(
    manifest: Mapping[str, Any], inventory: Sequence[Mapping[str, object]]
) -> None:
    projection = manifest.get("public_projection")
    if not isinstance(projection, Mapping):
        raise PublicationArtifactError(
            _ARTIFACT_CORRUPT,
            "The artifact public projection summary is invalid and cannot be verified.",
        )
    raw_inventory = projection.get("inventory")
    privacy = projection.get("privacy_scan")
    if not isinstance(raw_inventory, Mapping) or not isinstance(privacy, Mapping):
        raise PublicationArtifactError(
            _ARTIFACT_CORRUPT,
            "The artifact public projection inventory is invalid and cannot be verified.",
        )
    if privacy.get("status") != "pass":
        raise PublicationArtifactError(
            _ARTIFACT_CORRUPT,
            "The artifact privacy scan is not verified for public delivery.",
        )
    artifact_paths = {str(item.get("path") or "") for item in inventory}
    for name, raw_paths in raw_inventory.items():
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(raw_paths, list)
            or not raw_paths
            or any(not isinstance(path, str) or path not in artifact_paths for path in raw_paths)
        ):
            raise PublicationArtifactError(
                _ARTIFACT_CORRUPT,
                "The artifact public projection inventory is invalid and cannot be verified.",
            )


def _verify_public_projection(source_root: Path, output_root: Path) -> dict[str, int | str]:
    records: list[tuple[Path, dict[str, Any], str]] = []
    public_text: list[str] = []
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".mdx"} or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        meta, body = _frontmatter(text)
        records.append((path, meta, body))
        if is_public_meta(meta):
            public_text.append(text)
    public = "\n".join(public_text)
    canaries: list[VisibilityCanary] = []
    for path, meta, body in records:
        if is_public_meta(meta):
            continue
        candidates = [str(meta.get("title") or "")]
        candidates.extend(line.strip().strip("#").strip() for line in body.splitlines())
        tokens = tuple(
            dict.fromkeys(
                token for token in candidates if 12 <= len(token) <= 240 and token not in public
            )
        )
        if tokens:
            canaries.append(
                VisibilityCanary(
                    source_path=path.relative_to(source_root).as_posix(),
                    boundary=str(meta.get("visibility") or "protected"),
                    tokens=tokens,
                )
            )
    report = scan_visibility_leaks(output_root, canaries)
    if not report.ok:
        raise PublicationArtifactError(
            _PRIVACY_SCAN_FAILED,
            "A protected-content canary appeared in a public publication artifact.",
        )
    return {"status": "pass", "canaries": len(canaries), "artifacts": report.scanned_artifacts}


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}, text
    try:
        raw = yaml.safe_load(parts[0][4:]) or {}
    except yaml.YAMLError:
        return {}, text
    return (dict(raw) if isinstance(raw, Mapping) else {}), parts[1]


def _verify_clean_checkout(root: Path, commit: str) -> None:
    if _git_output(("rev-parse", "HEAD"), cwd=root) != commit:
        raise PublicationArtifactError(
            _SOURCE_REVISION_MISMATCH,
            "The isolated checkout no longer matches the approved exact commit.",
        )
    symbolic = subprocess.run(
        ("git", "symbolic-ref", "-q", "HEAD"),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    dirty = _git_output(("status", "--porcelain=v1", "--untracked-files=all"), cwd=root)
    if symbolic.returncode == 0 or dirty:
        raise PublicationArtifactError(
            _SOURCE_CHECKOUT_DIRTY,
            "The publication build source is not a clean detached checkout.",
        )


def _git_output(arguments: tuple[str, ...], *, cwd: Path) -> str:
    result = _run_git(
        arguments,
        cwd=cwd,
        code=_SOURCE_CHECKOUT_INVALID,
        message="The publication source checkout could not be verified.",
    )
    return result.stdout.strip().lower()


def _run_git(
    arguments: tuple[str, ...],
    *,
    cwd: Path,
    code: str,
    message: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ("git", *arguments),
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PublicationArtifactError(code, message) from exc


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _key_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _commit(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if _COMMIT.fullmatch(normalized) is None:
        raise ValueError("Publication artifact source commits must be exact 40-character Git SHAs.")
    return normalized


def _digest(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if _DIGEST.fullmatch(normalized) is None:
        raise ValueError(
            "Publication artifact digests must use a complete SHA-256 content address."
        )
    return normalized


def _required(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(
            f"Publication artifact {label} is required to identify the approved build."
        )
    return normalized


def _string_mapping(value: Mapping[str, str], label: str) -> dict[str, str]:
    return {
        _required(key, f"{label} key"): _required(item, f"{label} value")
        for key, item in value.items()
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _write_json_once(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(dict(value), sort_keys=True, separators=(",", ":")) + "\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = _read_json(path)
        if existing != dict(value):
            raise PublicationArtifactError(
                _ARTIFACT_CONFLICT,
                "An immutable artifact record already contains different persisted content.",
            ) from None
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json_replace(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(value), indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
