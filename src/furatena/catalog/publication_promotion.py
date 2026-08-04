"""Provider-neutral promotion of one verified artifact across environments."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from furatena.catalog.capability_policy import (
    Capability,
    CapabilityDecision,
    CapabilityRequest,
)
from furatena.catalog.operation_lease import (
    OperationLease,
    operation_lease_seconds,
    operation_timeout_seconds,
)
from furatena.catalog.publication_approvals import PublicationApprovalEvaluation
from furatena.catalog.publication_contracts import (
    PublicationActor,
    PublicationPlan,
    canonical_json_bytes,
    normalize_rfc3339,
    sha256_digest,
)

PUBLICATION_PROMOTION_SCHEMA_VERSION = 1
_ARTIFACT_ID = re.compile(r"^publication-artifact-[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_OPERATION_ID = re.compile(r"^promotion-operation-[0-9a-f]{24}$")
_DEPLOYED_ENVIRONMENTS = frozenset({"preview", "staging", "production"})
_OPERATION_NOT_FOUND = "operation_not_found"


class PromotionEnvironment(StrEnum):
    ARTIFACT = "artifact"
    PREVIEW = "preview"
    STAGING = "staging"
    PRODUCTION = "production"


class PromotionOperation(StrEnum):
    PROMOTE = "promote"
    ROLLBACK = "rollback"


class PromotionStrategy(StrEnum):
    ATOMIC = "atomic"
    SAFE_CANARY = "safe_canary"


class PromotionStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class PublicationPromotionError(RuntimeError):
    """Sanitized promotion failure with a stable recovery code."""

    def __init__(self, code: str, message: str, remediation: str) -> None:
        self.code = _required(code, "error code")
        self.remediation = _required(remediation, "remediation")
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PromotionArtifact:
    artifact_id: str
    artifact_digest: str
    manifest_digest: str
    plan_id: str
    plan_digest: str
    policy_version: str
    policy_digest: str
    configuration_digest: str
    presentation_digest: str
    runtime_identity: Mapping[str, str]
    fingerprints: Mapping[str, str]
    public_projection_digest: str

    def __post_init__(self) -> None:
        if _ARTIFACT_ID.fullmatch(self.artifact_id) is None:
            raise ValueError(
                "Promotion artifacts require a verified content-addressed identity before deployment."
            )
        for name in (
            "artifact_digest",
            "manifest_digest",
            "plan_digest",
            "policy_digest",
            "configuration_digest",
            "presentation_digest",
            "public_projection_digest",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name)))
        for name in ("plan_id", "policy_version"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        for name in ("runtime_identity", "fingerprints"):
            value = {str(key): _required(item, name) for key, item in getattr(self, name).items()}
            if not value:
                raise ValueError(
                    f"The promotion artifact {name} field requires at least one value."
                )
            object.__setattr__(self, name, value)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PromotionArtifact:
        return cls(
            artifact_id=str(value.get("artifact_id") or ""),
            artifact_digest=str(value.get("artifact_digest") or ""),
            manifest_digest=str(value.get("manifest_digest") or ""),
            plan_id=str(value.get("plan_id") or ""),
            plan_digest=str(value.get("plan_digest") or ""),
            policy_version=str(value.get("policy_version") or ""),
            policy_digest=str(value.get("policy_digest") or ""),
            configuration_digest=str(value.get("configuration_digest") or ""),
            presentation_digest=str(value.get("presentation_digest") or ""),
            runtime_identity=_string_mapping(value.get("runtime_identity"), "runtime_identity"),
            fingerprints=_string_mapping(value.get("fingerprints"), "fingerprints"),
            public_projection_digest=str(value.get("public_projection_digest") or ""),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_digest": self.artifact_digest,
            "manifest_digest": self.manifest_digest,
            "plan_id": self.plan_id,
            "plan_digest": self.plan_digest,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "configuration_digest": self.configuration_digest,
            "presentation_digest": self.presentation_digest,
            "runtime_identity": dict(self.runtime_identity),
            "fingerprints": dict(self.fingerprints),
            "public_projection_digest": self.public_projection_digest,
        }


@dataclass(frozen=True, slots=True)
class PromotionDeployment:
    environment: PromotionEnvironment
    artifact_id: str
    artifact_digest: str
    manifest_digest: str
    generation: int
    serving: bool
    deployed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", PromotionEnvironment(self.environment))
        if self.environment.value not in _DEPLOYED_ENVIRONMENTS:
            raise ValueError("Deployment state requires one of preview, staging, or production.")
        if _ARTIFACT_ID.fullmatch(self.artifact_id) is None:
            raise ValueError(
                "Deployment state requires a fully verified publication artifact identifier."
            )
        object.__setattr__(self, "artifact_digest", _digest(self.artifact_digest))
        object.__setattr__(self, "manifest_digest", _digest(self.manifest_digest))
        if self.generation < 1:
            raise ValueError(
                "Deployment generation values must be positive integers before activation."
            )
        object.__setattr__(self, "deployed_at", normalize_rfc3339(self.deployed_at))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PromotionDeployment:
        return cls(
            environment=PromotionEnvironment(str(value.get("environment") or "")),
            artifact_id=str(value.get("artifact_id") or ""),
            artifact_digest=str(value.get("artifact_digest") or ""),
            manifest_digest=str(value.get("manifest_digest") or ""),
            generation=int(value.get("generation") or 0),
            serving=bool(value.get("serving")),
            deployed_at=str(value.get("deployed_at") or ""),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "environment": self.environment.value,
            "artifact_id": self.artifact_id,
            "artifact_digest": self.artifact_digest,
            "manifest_digest": self.manifest_digest,
            "generation": self.generation,
            "serving": self.serving,
            "deployed_at": self.deployed_at,
        }

    def same_artifact(self, artifact: PromotionArtifact) -> bool:
        return bool(
            self.serving
            and self.artifact_id == artifact.artifact_id
            and self.artifact_digest == artifact.artifact_digest
            and self.manifest_digest == artifact.manifest_digest
        )


@dataclass(frozen=True, slots=True)
class PromotionPreflight:
    strategy: PromotionStrategy
    compatible: bool
    environment_policy: bool
    current_state_matches: bool
    checked_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy", PromotionStrategy(self.strategy))
        object.__setattr__(self, "checked_at", normalize_rfc3339(self.checked_at))

    @property
    def ok(self) -> bool:
        return self.compatible and self.environment_policy and self.current_state_matches

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy.value,
            "compatible": self.compatible,
            "environment_policy": self.environment_policy,
            "current_state_matches": self.current_state_matches,
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PromotionPreflight:
        return cls(
            strategy=PromotionStrategy(str(value.get("strategy") or "")),
            compatible=bool(value.get("compatible")),
            environment_policy=bool(value.get("environment_policy")),
            current_state_matches=bool(value.get("current_state_matches")),
            checked_at=str(value.get("checked_at") or ""),
        )


@dataclass(frozen=True, slots=True)
class PromotionVerification:
    artifact_digest: str
    manifest_digest: str
    checks: Mapping[str, bool]
    smoke: Mapping[str, bool]
    evidence_digest: str
    verified_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_digest", _digest(self.artifact_digest))
        object.__setattr__(self, "manifest_digest", _digest(self.manifest_digest))
        required = {"readiness", "build_identity", "public_projection", "privacy"}
        checks = {str(key): bool(value) for key, value in self.checks.items()}
        if set(checks) != required:
            raise ValueError(
                "Promotion verification requires every defined serving safety check result."
            )
        object.__setattr__(self, "checks", checks)
        smoke = {str(key): bool(value) for key, value in self.smoke.items()}
        object.__setattr__(self, "smoke", smoke)
        object.__setattr__(self, "evidence_digest", _digest(self.evidence_digest))
        object.__setattr__(self, "verified_at", normalize_rfc3339(self.verified_at))

    @property
    def ok(self) -> bool:
        return all(self.checks.values()) and all(self.smoke.values())

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_digest": self.artifact_digest,
            "manifest_digest": self.manifest_digest,
            "checks": dict(self.checks),
            "smoke": dict(self.smoke),
            "evidence_digest": self.evidence_digest,
            "verified_at": self.verified_at,
        }


class PublicationPromotionBackend(Protocol):
    """Trusted provider adapter; implementations own no policy decisions."""

    def inspect(self, environment: PromotionEnvironment, /) -> PromotionDeployment | None: ...

    def preflight(
        self,
        environment: PromotionEnvironment,
        artifact: PromotionArtifact,
        current: PromotionDeployment | None,
        /,
    ) -> PromotionPreflight: ...

    def activate(
        self,
        environment: PromotionEnvironment,
        artifact: PromotionArtifact,
        current: PromotionDeployment | None,
        operation_id: str,
        strategy: PromotionStrategy,
        /,
    ) -> PromotionDeployment: ...

    def verify(
        self,
        environment: PromotionEnvironment,
        artifact: PromotionArtifact,
        smoke_checks: tuple[str, ...],
        operation_id: str,
        /,
    ) -> PromotionVerification: ...

    def restore(
        self,
        environment: PromotionEnvironment,
        previous: PromotionDeployment | None,
        operation_id: str,
        /,
    ) -> PromotionDeployment | None: ...


class PublicationArtifactResolver(Protocol):
    def promotion_identity(self, artifact_id: str, /) -> Mapping[str, object]: ...


type ApprovalCurrentChecker = Callable[[PublicationPlan, PublicationApprovalEvaluation], bool]


@dataclass(frozen=True, slots=True)
class PublicationPromotionCommand:
    operation: PromotionOperation
    artifact_id: str
    source_environment: PromotionEnvironment
    destination_environment: PromotionEnvironment
    actor: PublicationActor
    plan: PublicationPlan
    approval_evaluation: PublicationApprovalEvaluation
    capability_request: CapabilityRequest
    capability_decision: CapabilityDecision
    idempotency_key: str
    expected_destination_generation: int
    smoke_checks: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", PromotionOperation(self.operation))
        object.__setattr__(
            self, "source_environment", PromotionEnvironment(self.source_environment)
        )
        object.__setattr__(
            self, "destination_environment", PromotionEnvironment(self.destination_environment)
        )
        if _ARTIFACT_ID.fullmatch(self.artifact_id) is None:
            raise ValueError(
                "The promotion command requires a fully verified publication artifact identifier."
            )
        object.__setattr__(self, "idempotency_key", _required(self.idempotency_key, "idempotency"))
        if self.expected_destination_generation < 0:
            raise ValueError("The expected destination generation must be a non-negative integer.")
        smoke = tuple(sorted({_required(item, "smoke check") for item in self.smoke_checks}))
        object.__setattr__(self, "smoke_checks", smoke)
        object.__setattr__(self, "reason", self.reason.strip())
        self._validate_route()
        self._validate_authorization()

    def _validate_route(self) -> None:
        if self.operation == PromotionOperation.PROMOTE:
            routes = {
                (PromotionEnvironment.ARTIFACT, PromotionEnvironment.PREVIEW),
                (PromotionEnvironment.PREVIEW, PromotionEnvironment.STAGING),
                (PromotionEnvironment.STAGING, PromotionEnvironment.PRODUCTION),
            }
            if (self.source_environment, self.destination_environment) not in routes:
                raise ValueError(
                    "promotion must follow artifact, preview, staging, production order"
                )
            if self.reason:
                raise ValueError("promotion reason is reserved for rollback records")
        elif (
            self.source_environment != self.destination_environment
            or self.destination_environment.value not in _DEPLOYED_ENVIRONMENTS
            or not self.reason
        ):
            raise ValueError("rollback requires one deployed environment and a non-empty reason")

    def _validate_authorization(self) -> None:
        request = self.capability_request
        decision = self.capability_decision
        expected_capability = (
            Capability.PROMOTE
            if self.operation == PromotionOperation.PROMOTE
            else Capability.ROLL_BACK
        )
        if (
            not request.subject.trusted
            or request.subject.actor != self.actor.actor
            or request.subject.identity_source != self.actor.identity_source
            or request.capability != expected_capability
            or request.scope.environment != self.destination_environment.value
            or request.scope.tenant != self.plan.identity.tenant
            or request.scope.workspace != self.plan.identity.workspace
            or request.scope.site != self.plan.identity.site
            or request.plan_digest != self.plan.plan_digest
        ):
            raise ValueError("promotion authorization is not bound to the trusted actor and scope")
        if (
            not decision.allowed
            or decision.capability != expected_capability
            or decision.plan_digest != self.plan.plan_digest
            or decision.policy_version != self.plan.bindings.policy_version
            or decision.policy_digest != self.plan.bindings.policy_digest
            or decision.approval_requirements != self.plan.approval_requirements
        ):
            raise ValueError("promotion policy decision is stale, denied, or bound elsewhere")
        approval = self.approval_evaluation
        if (
            not approval.satisfied
            or approval.plan_digest != self.plan.plan_digest
            or approval.policy_version != self.plan.bindings.policy_version
            or approval.policy_digest != self.plan.bindings.policy_digest
            or len(approval.approval_record_ids) < self.plan.approval_requirements.required_count
        ):
            raise ValueError("promotion approvals are incomplete or stale")


@dataclass(frozen=True, slots=True)
class PublicationPromotionTransportAdapter:
    """Read parity for every transport; mutation belongs only to trusted automation."""

    transport: Literal["browser", "cli", "mcp", "automation"]
    service: PublicationPromotionService
    deploy_authority: bool = False

    def __post_init__(self) -> None:
        if self.deploy_authority and self.transport != "automation":
            raise ValueError(
                "Deployment authority is limited to the trusted automation transport only."
            )

    def current(self, environment: PromotionEnvironment) -> dict[str, object] | None:
        return self.service.current(environment, projection="display")

    def history(self, environment: PromotionEnvironment) -> tuple[dict[str, object], ...]:
        return self.service.history(environment, projection="display")

    def status(self, operation_id: str) -> dict[str, object]:
        return self.service.status(operation_id, projection="display")

    def promote(self, command: PublicationPromotionCommand) -> dict[str, object]:
        self._require_deploy_authority()
        return self.service.promote(command)

    def rollback(self, command: PublicationPromotionCommand) -> dict[str, object]:
        self._require_deploy_authority()
        return self.service.rollback(command)

    def _require_deploy_authority(self) -> None:
        if not self.deploy_authority:
            raise PublicationPromotionError(
                "transport_read_only",
                f"The {self.transport} promotion transport is read-only.",
                "Submit mutation through trusted deployment automation.",
            )


class JsonDirectoryPublicationPromotionStore:
    """Restart-safe private records for promotion current state and history."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.requests = self.root / "requests"
        self.operations = self.root / "operations"
        self.current_root = self.root / "current"
        self.history_root = self.root / "history"
        self.leases = self.root / "leases"
        self._lock = threading.RLock()

    def lease(self) -> OperationLease:
        return OperationLease(
            self.leases,
            "publication-promotion",
            resource=str(self.root),
            timeout_seconds=operation_timeout_seconds(),
            lease_seconds=operation_lease_seconds(),
        )

    def request_for_key(self, idempotency_digest: str) -> dict[str, Any] | None:
        return _read_json(self.requests / f"{idempotency_digest.removeprefix('sha256:')}.json")

    def write_request(self, idempotency_digest: str, value: Mapping[str, Any]) -> None:
        _write_json_once(
            self.requests / f"{idempotency_digest.removeprefix('sha256:')}.json", value
        )

    def operation(self, operation_id: str) -> dict[str, Any] | None:
        if _OPERATION_ID.fullmatch(operation_id) is None:
            raise PublicationPromotionError(
                _OPERATION_NOT_FOUND,
                "The promotion operation identifier is invalid.",
                "Use an operation ID returned by promotion automation.",
            )
        return _read_json(self.operations / f"{operation_id}.json")

    def write_operation(self, operation_id: str, value: Mapping[str, Any]) -> None:
        _write_json_replace(self.operations / f"{operation_id}.json", value)

    def current(self, environment: PromotionEnvironment) -> dict[str, Any] | None:
        environment = PromotionEnvironment(environment)
        if environment.value not in _DEPLOYED_ENVIRONMENTS:
            return None
        return _read_json(self.current_root / f"{environment.value}.json")

    def write_current(self, environment: PromotionEnvironment, value: Mapping[str, Any]) -> None:
        _write_json_replace(self.current_root / f"{environment.value}.json", value)

    def append_history(
        self, environment: PromotionEnvironment, generation: int, value: Mapping[str, Any]
    ) -> None:
        operation_id = str(value.get("operation_id") or "")
        _write_json_once(
            self.history_root / environment.value / f"{generation:012d}-{operation_id}.json",
            value,
        )

    def history(self, environment: PromotionEnvironment) -> tuple[dict[str, Any], ...]:
        root = self.history_root / PromotionEnvironment(environment).value
        return tuple(
            value
            for path in sorted(root.glob("*.json"))
            if root.is_dir()
            if (value := _read_json(path)) is not None
        )

    def known_artifact(self, environment: PromotionEnvironment, artifact_id: str) -> bool:
        return any(
            record.get("status") == PromotionStatus.SUCCEEDED.value
            and isinstance(record.get("artifact"), Mapping)
            and record["artifact"].get("artifact_id") == artifact_id
            for record in self.history(environment)
        )


class PublicationPromotionService:
    """Promote and roll back verified artifacts without rebuilding them."""

    def __init__(
        self,
        *,
        store: JsonDirectoryPublicationPromotionStore,
        artifact_resolver: PublicationArtifactResolver,
        backend: PublicationPromotionBackend,
        approval_current: ApprovalCurrentChecker,
        clock: Callable[[], str],
    ) -> None:
        self.store = store
        self.artifact_resolver = artifact_resolver
        self.backend = backend
        self.approval_current = approval_current
        self.clock = clock
        self._lock = threading.RLock()

    def promote(self, command: PublicationPromotionCommand) -> dict[str, object]:
        if command.operation != PromotionOperation.PROMOTE:
            raise ValueError(
                "The promote operation requires a matching publication promotion command."
            )
        return self._run(command)

    def rollback(self, command: PublicationPromotionCommand) -> dict[str, object]:
        if command.operation != PromotionOperation.ROLLBACK:
            raise ValueError(
                "The rollback operation requires a matching publication rollback command."
            )
        return self._run(command)

    def current(
        self,
        environment: PromotionEnvironment,
        *,
        projection: Literal["trusted", "display"] = "display",
    ) -> dict[str, object] | None:
        record = self.store.current(environment)
        return None if record is None else _project(record, projection)

    def history(
        self,
        environment: PromotionEnvironment,
        *,
        projection: Literal["trusted", "display"] = "display",
    ) -> tuple[dict[str, object], ...]:
        return tuple(_project(item, projection) for item in self.store.history(environment))

    def status(
        self,
        operation_id: str,
        *,
        projection: Literal["trusted", "display"] = "display",
    ) -> dict[str, object]:
        record = self.store.operation(operation_id)
        if record is None:
            raise PublicationPromotionError(
                _OPERATION_NOT_FOUND,
                "The promotion operation does not exist.",
                "Use an operation ID returned by promotion automation.",
            )
        return _project(record, projection)

    def _run(self, command: PublicationPromotionCommand) -> dict[str, object]:
        with self._lock, self.store.lease():
            artifact = self._artifact(command)
            idempotency_digest = _key_digest(command.idempotency_key)
            request = self._request_record(command, artifact, idempotency_digest)
            existing_request = self.store.request_for_key(idempotency_digest)
            if existing_request is not None:
                if existing_request.get("request_digest") != request["request_digest"]:
                    raise PublicationPromotionError(
                        "idempotency_conflict",
                        "The promotion idempotency key is bound to another request.",
                        "Use a fresh idempotency key for different promotion input.",
                    )
                operation_id = str(existing_request.get("operation_id") or "")
                receipt = self.store.operation(operation_id)
                if receipt is None:
                    self._require_current_approvals(command)
                    return self._start(command, artifact, request)
                if receipt.get("status") != PromotionStatus.STARTED.value:
                    self._ensure_history(receipt)
                    return {**_project(receipt, "trusted"), "replayed": True}
                self._require_current_approvals(command)
                return self._resume(command, artifact, request, receipt)
            self._require_current_approvals(command)
            self.store.write_request(idempotency_digest, request)
            return self._start(command, artifact, request)

    def _require_current_approvals(self, command: PublicationPromotionCommand) -> None:
        if not self.approval_current(command.plan, command.approval_evaluation):
            raise PublicationPromotionError(
                "approval_stale",
                "Promotion approvals are no longer current.",
                "Collect current approvals bound to the same plan and policy.",
            )

    def _artifact(self, command: PublicationPromotionCommand) -> PromotionArtifact:
        try:
            artifact = PromotionArtifact.from_dict(
                self.artifact_resolver.promotion_identity(command.artifact_id)
            )
        except Exception as exc:
            raise PublicationPromotionError(
                "artifact_integrity_failed",
                "The selected immutable publication artifact failed full verification.",
                "Select a verified artifact produced from the approved exact revision.",
            ) from exc
        if (
            artifact.plan_id != command.plan.plan_id
            or artifact.plan_digest != command.plan.plan_digest
            or artifact.policy_version != command.plan.bindings.policy_version
            or artifact.policy_digest != command.plan.bindings.policy_digest
        ):
            raise PublicationPromotionError(
                "artifact_binding_stale",
                "The artifact is not bound to the current approved plan and policy.",
                "Build a fresh artifact from the current approved plan.",
            )
        return artifact

    def _start(
        self,
        command: PublicationPromotionCommand,
        artifact: PromotionArtifact,
        request: Mapping[str, Any],
    ) -> dict[str, object]:
        operation_id = str(request["operation_id"])
        try:
            previous = self._preflight_state(command, artifact)
            preflight = self.backend.preflight(command.destination_environment, artifact, previous)
        except Exception as exc:
            preflight = PromotionPreflight(
                strategy=PromotionStrategy.ATOMIC,
                compatible=False,
                environment_policy=False,
                current_state_matches=False,
                checked_at=self.clock(),
            )
            started = self._receipt(
                command,
                artifact,
                request,
                status=PromotionStatus.STARTED,
                previous=None,
                preflight=preflight,
            )
            self.store.write_operation(operation_id, started)
            known_error = isinstance(exc, PublicationPromotionError)
            return self._finish_failure(
                started,
                code=exc.code if known_error else "preflight_failed",
                message=(
                    str(exc)
                    if known_error
                    else "Promotion preflight could not verify the destination."
                ),
                remediation=str(
                    getattr(
                        exc,
                        "remediation",
                        "Resolve compatibility, policy, or current-state drift and retry.",
                    )
                ),
            )
        started = self._receipt(
            command,
            artifact,
            request,
            status=PromotionStatus.STARTED,
            previous=previous,
            preflight=preflight,
        )
        self.store.write_operation(operation_id, started)
        if not preflight.ok:
            return self._finish_failure(
                started,
                code="preflight_failed",
                message="Promotion preflight rejected the artifact or destination state.",
                remediation="Resolve compatibility, policy, or current-state drift and retry.",
            )
        return self._activate(command, artifact, started, previous, preflight)

    def _resume(
        self,
        command: PublicationPromotionCommand,
        artifact: PromotionArtifact,
        request: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> dict[str, object]:
        previous_raw = receipt.get("previous")
        previous = (
            PromotionDeployment.from_dict(previous_raw)
            if isinstance(previous_raw, Mapping)
            else None
        )
        preflight_raw = receipt.get("preflight")
        if not isinstance(preflight_raw, Mapping):
            return self._finish_failure(
                receipt,
                code="operation_corrupt",
                message="The interrupted promotion receipt lacks preflight evidence.",
                remediation="Reconcile the destination and submit a fresh authorized request.",
                reconciliation_required=True,
            )
        preflight = PromotionPreflight.from_dict(preflight_raw)
        observed = self.backend.inspect(command.destination_environment)
        if observed is not None and observed.same_artifact(artifact):
            return self._verify_and_commit(command, artifact, receipt, previous)
        if _same_deployment(observed, previous):
            return self._activate(command, artifact, receipt, previous, preflight)
        return self._recover(
            command,
            artifact,
            receipt,
            previous,
            code="restart_state_unknown",
            message="Restart reconciliation found an unexpected serving artifact.",
        )

    def _activate(
        self,
        command: PublicationPromotionCommand,
        artifact: PromotionArtifact,
        receipt: Mapping[str, Any],
        previous: PromotionDeployment | None,
        preflight: PromotionPreflight,
    ) -> dict[str, object]:
        try:
            deployed = self.backend.activate(
                command.destination_environment,
                artifact,
                previous,
                str(receipt["operation_id"]),
                preflight.strategy,
            )
            if not deployed.same_artifact(artifact):
                raise PublicationPromotionError(
                    "deployed_identity_mismatch",
                    "The serving deployment does not match the selected artifact.",
                    "Restore last-known-good and inspect deployment identity evidence.",
                )
            return self._verify_and_commit(command, artifact, receipt, previous)
        except Exception as exc:
            code = exc.code if isinstance(exc, PublicationPromotionError) else "deployment_failed"
            return self._recover(
                command,
                artifact,
                receipt,
                previous,
                code=str(code),
                message="The destination could not activate the verified artifact.",
            )

    def _verify_and_commit(
        self,
        command: PublicationPromotionCommand,
        artifact: PromotionArtifact,
        receipt: Mapping[str, Any],
        previous: PromotionDeployment | None,
    ) -> dict[str, object]:
        verification: PromotionVerification | None = None
        try:
            verification = self.backend.verify(
                command.destination_environment,
                artifact,
                command.smoke_checks,
                str(receipt["operation_id"]),
            )
            if (
                not verification.ok
                or verification.artifact_digest != artifact.artifact_digest
                or verification.manifest_digest != artifact.manifest_digest
            ):
                raise PublicationPromotionError(
                    "verification_failed",
                    "The deployed artifact failed serving verification.",
                    "Restore last-known-good and inspect the failed verification evidence.",
                )
        except Exception as exc:
            return self._recover(
                command,
                artifact,
                receipt,
                previous,
                code=(
                    exc.code
                    if isinstance(exc, PublicationPromotionError)
                    else "verification_failed"
                ),
                message="The deployed artifact failed serving verification.",
                verification=verification,
            )
        observed = self.backend.inspect(command.destination_environment)
        if observed is None or not observed.same_artifact(artifact):
            return self._recover(
                command,
                artifact,
                receipt,
                previous,
                code="serving_state_changed",
                message="The serving artifact changed during verification.",
            )
        return self._finish_success(command, artifact, receipt, previous, observed, verification)

    def _recover(
        self,
        command: PublicationPromotionCommand,
        artifact: PromotionArtifact,
        receipt: Mapping[str, Any],
        previous: PromotionDeployment | None,
        *,
        code: str,
        message: str,
        verification: PromotionVerification | None = None,
    ) -> dict[str, object]:
        restoration: dict[str, object]
        try:
            observed = self.backend.inspect(command.destination_environment)
            restored = (
                observed
                if _same_deployment(observed, previous)
                else self.backend.restore(
                    command.destination_environment,
                    previous,
                    str(receipt["operation_id"]),
                )
            )
            if not _same_deployment(restored, previous):
                raise RuntimeError("restored deployment identity does not match last-known-good")
            restoration = {"status": "restored", "deployment": _deployment_dict(restored)}
        except Exception:
            restoration = {"status": "unknown", "deployment": None}
            return self._finish_failure(
                receipt,
                code=code,
                message=message,
                remediation="Reconcile serving state and restore the recorded last-known-good.",
                restoration=restoration,
                reconciliation_required=True,
                verification=verification,
            )
        return self._finish_failure(
            receipt,
            code=code,
            message=message,
            remediation="Inspect the failed deployment evidence before retrying.",
            restoration=restoration,
            verification=verification,
        )

    def _finish_success(
        self,
        command: PublicationPromotionCommand,
        artifact: PromotionArtifact,
        receipt: Mapping[str, Any],
        previous: PromotionDeployment | None,
        deployed: PromotionDeployment,
        verification: PromotionVerification,
    ) -> dict[str, object]:
        stored = self.store.current(command.destination_environment)
        if stored is not None and stored.get("operation_id") == receipt["operation_id"]:
            current = dict(stored)
            generation = int(current["generation"])
        else:
            generation = int(stored.get("generation") or 0) + 1 if stored else 1
            last_known_good_raw = stored.get("last_known_good") if stored else None
            last_known_good = (
                previous.to_dict()
                if previous is not None and previous.artifact_id != artifact.artifact_id
                else last_known_good_raw
            )
            current = {
                "schema_version": PUBLICATION_PROMOTION_SCHEMA_VERSION,
                "record_type": "furatena.publication.promotion-current",
                "environment": command.destination_environment.value,
                "generation": generation,
                "artifact": artifact.to_dict(),
                "deployment": deployed.to_dict(),
                "last_known_good": last_known_good,
                "operation_id": receipt["operation_id"],
                "updated_at": normalize_rfc3339(self.clock()),
            }
        completed = {
            **dict(receipt),
            "status": PromotionStatus.SUCCEEDED.value,
            "history_generation": generation,
            "verification": verification.to_dict(),
            "result": current,
            "error": None,
            "completed_at": normalize_rfc3339(self.clock()),
        }
        self.store.write_current(command.destination_environment, current)
        self.store.write_operation(str(receipt["operation_id"]), completed)
        self.store.append_history(command.destination_environment, generation, completed)
        return {**_project(completed, "trusted"), "replayed": False}

    def _finish_failure(
        self,
        receipt: Mapping[str, Any],
        *,
        code: str,
        message: str,
        remediation: str,
        restoration: Mapping[str, Any] | None = None,
        reconciliation_required: bool = False,
        verification: PromotionVerification | None = None,
    ) -> dict[str, object]:
        status = (
            PromotionStatus.RECONCILIATION_REQUIRED
            if reconciliation_required
            else PromotionStatus.FAILED
        )
        completed = {
            **dict(receipt),
            "status": status.value,
            "verification": verification.to_dict() if verification is not None else None,
            "restoration": dict(restoration or {"status": "not_required", "deployment": None}),
            "error": {
                "code": _required(code, "failure code"),
                "message": _required(message, "failure message"),
                "remediation": _required(remediation, "failure remediation"),
            },
            "completed_at": normalize_rfc3339(self.clock()),
        }
        environment = PromotionEnvironment(str(receipt["destination_environment"]))
        generation = int((self.store.current(environment) or {}).get("generation") or 0)
        completed["history_generation"] = generation + 1
        self.store.write_operation(str(receipt["operation_id"]), completed)
        self.store.append_history(environment, generation + 1, completed)
        return {**_project(completed, "trusted"), "replayed": False}

    def _ensure_history(self, receipt: Mapping[str, Any]) -> None:
        generation = int(receipt.get("history_generation") or 0)
        if generation < 1:
            raise PublicationPromotionError(
                "record_corrupt",
                "A completed promotion receipt lacks its history generation.",
                "Restore the record from trusted backup before replaying it.",
            )
        environment = PromotionEnvironment(str(receipt["destination_environment"]))
        self.store.append_history(environment, generation, receipt)

    def _preflight_state(
        self, command: PublicationPromotionCommand, artifact: PromotionArtifact
    ) -> PromotionDeployment | None:
        destination = command.destination_environment
        stored = self.store.current(destination)
        recorded = _current_deployment(stored)
        observed = self.backend.inspect(destination)
        if not _same_deployment(recorded, observed):
            raise PublicationPromotionError(
                "current_state_mismatch",
                "Durable and serving destination state do not match.",
                "Reconcile the destination before promotion.",
            )
        generation = int(stored.get("generation") or 0) if stored else 0
        if generation != command.expected_destination_generation:
            raise PublicationPromotionError(
                "destination_generation_stale",
                "The destination generation changed before promotion.",
                "Reload current state and submit a fresh request.",
            )
        if command.operation == PromotionOperation.ROLLBACK:
            if not self.store.known_artifact(destination, artifact.artifact_id):
                raise PublicationPromotionError(
                    "rollback_artifact_unknown",
                    "Rollback selected an artifact absent from successful destination history.",
                    "Select a known verified artifact from destination history.",
                )
            return observed
        if command.source_environment != PromotionEnvironment.ARTIFACT:
            source = self.backend.inspect(command.source_environment)
            source_record = self.store.current(command.source_environment)
            if (
                source is None
                or not source.same_artifact(artifact)
                or not _same_deployment(source, _current_deployment(source_record))
            ):
                raise PublicationPromotionError(
                    "source_environment_mismatch",
                    "The source environment is not serving the selected artifact.",
                    "Promote the same digest through each environment in order.",
                )
        return observed

    def _request_record(
        self,
        command: PublicationPromotionCommand,
        artifact: PromotionArtifact,
        idempotency_digest: str,
    ) -> dict[str, Any]:
        payload = {
            "schema_version": PUBLICATION_PROMOTION_SCHEMA_VERSION,
            "record_type": "furatena.publication.promotion-request",
            "operation": command.operation.value,
            "artifact": artifact.to_dict(),
            "source_environment": command.source_environment.value,
            "destination_environment": command.destination_environment.value,
            "actor": command.actor.to_dict(),
            "plan": command.plan.to_dict("trusted"),
            "approval_evaluation": command.approval_evaluation.to_dict(),
            "capability_request": command.capability_request.to_dict("trusted"),
            "capability_decision": command.capability_decision.to_dict(),
            "idempotency_key_digest": idempotency_digest,
            "expected_destination_generation": command.expected_destination_generation,
            "smoke_checks": list(command.smoke_checks),
            "reason": command.reason or None,
        }
        request_digest = sha256_digest(canonical_json_bytes(payload))
        operation_id = f"promotion-operation-{request_digest.removeprefix('sha256:')[:24]}"
        return {
            **payload,
            "request_digest": request_digest,
            "operation_id": operation_id,
            "recorded_at": normalize_rfc3339(self.clock()),
        }

    def _receipt(
        self,
        command: PublicationPromotionCommand,
        artifact: PromotionArtifact,
        request: Mapping[str, Any],
        *,
        status: PromotionStatus,
        previous: PromotionDeployment | None,
        preflight: PromotionPreflight,
    ) -> dict[str, Any]:
        approval_digest = sha256_digest(canonical_json_bytes(command.approval_evaluation.to_dict()))
        return {
            "schema_version": PUBLICATION_PROMOTION_SCHEMA_VERSION,
            "record_type": "furatena.publication.promotion-receipt",
            "operation_id": request["operation_id"],
            "request_digest": request["request_digest"],
            "idempotency_key_digest": request["idempotency_key_digest"],
            "operation": command.operation.value,
            "source_environment": command.source_environment.value,
            "destination_environment": command.destination_environment.value,
            "artifact": artifact.to_dict(),
            "actor": command.actor.to_dict(),
            "plan": {"plan_id": command.plan.plan_id, "plan_digest": command.plan.plan_digest},
            "approvals": {
                "evaluation_digest": approval_digest,
                "record_ids": list(command.approval_evaluation.approval_record_ids),
                "evaluated_at": command.approval_evaluation.evaluated_at,
            },
            "policy": {
                "capability": command.capability_decision.capability.value,
                "version": command.capability_decision.policy_version,
                "digest": command.capability_decision.policy_digest,
                "decision_scope_digest": command.capability_decision.decision_scope_digest,
                "reason_code": command.capability_decision.reason_code,
                "evaluated_at": command.capability_decision.evaluated_at,
            },
            "status": status.value,
            "preflight": preflight.to_dict(),
            "previous": _deployment_dict(previous),
            "verification": None,
            "restoration": None,
            "result": None,
            "reason": command.reason or None,
            "error": None,
            "started_at": normalize_rfc3339(self.clock()),
            "completed_at": None,
        }


class PublicationPromotionReader:
    """Read-only store facade for CLI, browser, MCP, and automation composition."""

    def __init__(self, root: Path) -> None:
        self.store = JsonDirectoryPublicationPromotionStore(root)

    def current(self, environment: PromotionEnvironment) -> dict[str, object] | None:
        value = self.store.current(environment)
        return None if value is None else _project(value, "display")

    def history(self, environment: PromotionEnvironment) -> tuple[dict[str, object], ...]:
        return tuple(_project(value, "display") for value in self.store.history(environment))

    def status(self, operation_id: str) -> dict[str, object]:
        value = self.store.operation(operation_id)
        if value is None:
            raise PublicationPromotionError(
                _OPERATION_NOT_FOUND,
                "The promotion operation does not exist.",
                "Use an operation ID returned by trusted automation.",
            )
        return _project(value, "display")


def _project(
    value: Mapping[str, Any], projection: Literal["trusted", "display"]
) -> dict[str, object]:
    if projection not in {"trusted", "display"}:
        raise ValueError("promotion projection must be trusted or display")
    payload = json.loads(json.dumps(dict(value)))
    if projection == "display":
        payload.pop("actor", None)
        payload.pop("idempotency_key_digest", None)
    return payload


def _current_deployment(value: Mapping[str, Any] | None) -> PromotionDeployment | None:
    if value is None:
        return None
    deployment = value.get("deployment")
    if (
        value.get("schema_version") != PUBLICATION_PROMOTION_SCHEMA_VERSION
        or value.get("record_type") != "furatena.publication.promotion-current"
        or not isinstance(deployment, Mapping)
    ):
        raise PublicationPromotionError(
            "record_corrupt",
            "A durable promotion current pointer has an invalid shape.",
            "Restore the current pointer from trusted history before retrying.",
        )
    try:
        return PromotionDeployment.from_dict(deployment)
    except (TypeError, ValueError) as exc:
        raise PublicationPromotionError(
            "record_corrupt",
            "A durable promotion current pointer has invalid deployment identity.",
            "Restore the current pointer from trusted history before retrying.",
        ) from exc


def _same_deployment(left: PromotionDeployment | None, right: PromotionDeployment | None) -> bool:
    if left is None or right is None:
        return left is right
    return left == right


def _deployment_dict(value: PromotionDeployment | None) -> dict[str, object] | None:
    return value.to_dict() if value is not None else None


def _key_digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _digest(value: str) -> str:
    normalized = str(value).strip().lower()
    if _DIGEST.fullmatch(normalized) is None:
        raise ValueError("promotion digest must be an exact lowercase SHA-256")
    return normalized


def _required(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"promotion {label} is required")
    return normalized


def _string_mapping(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"promotion artifact {label} must be an object")
    return {str(key): str(item) for key, item in value.items()}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationPromotionError(
            "record_corrupt",
            "A durable promotion record cannot be read.",
            "Restore the record from trusted backup before retrying.",
        ) from exc
    if not isinstance(value, dict):
        raise PublicationPromotionError(
            "record_corrupt",
            "A durable promotion record has an invalid shape.",
            "Restore the record from trusted backup before retrying.",
        )
    return value


def _write_json_once(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(dict(value), indent=2, sort_keys=True) + "\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = _read_json(path)
        if existing != dict(value):
            raise PublicationPromotionError(
                "record_conflict",
                "An immutable promotion record already exists with different content.",
                "Reconcile durable state before retrying.",
            ) from None
        return
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(path.parent)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _write_json_replace(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(json.dumps(dict(value), indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
