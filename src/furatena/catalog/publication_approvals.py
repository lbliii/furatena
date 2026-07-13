"""Durable, revision-bound publication approvals, waivers, and evaluation."""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Never, Protocol, cast

from furatena.catalog.audit_store import AuditStore
from furatena.catalog.publication_contracts import (
    PublicationActor,
    PublicationDecision,
    PublicationDecisionKind,
    PublicationPlan,
    canonical_json_bytes,
    normalize_rfc3339,
    sha256_digest,
)

PUBLICATION_APPROVAL_SCHEMA_VERSION = 1
type ApprovalProjection = Literal["trusted", "audit", "public"]

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_CONTROL_DECISIONS = frozenset(
    {
        PublicationDecisionKind.REVOKE,
        PublicationDecisionKind.EXPIRE,
        PublicationDecisionKind.SUPERSEDE,
    }
)


class PublicationApprovalErrorCode(str):
    AUTHORIZATION = "authorization_denied"
    INELIGIBLE = "ineligible_approver"
    SELF_APPROVAL = "self_approval_denied"
    INVALID_WAIVER = "invalid_warning_waiver"
    INVALID_TARGET = "invalid_decision_target"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    NOT_FOUND = "not_found"
    CORRUPT = "corrupt_record"


class PublicationApprovalError(RuntimeError):
    def __init__(self, code: str, message: str, remediation: str) -> None:
        self.code = code
        self.remediation = remediation
        super().__init__(message)

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": str(self),
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class PublicationApprovalScope:
    tenant: str
    workspace: str
    site: str
    mount: str
    node_id: str
    paths: tuple[str, ...]
    environments: tuple[str, ...]
    resulting_visibility: str

    def __post_init__(self) -> None:
        for name in (
            "tenant",
            "workspace",
            "site",
            "mount",
            "node_id",
            "resulting_visibility",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "paths", _sorted_required(self.paths, "paths"))
        object.__setattr__(self, "environments", _sorted_strings(self.environments))

    @classmethod
    def from_plan(cls, plan: PublicationPlan) -> PublicationApprovalScope:
        return cls(
            tenant=plan.identity.tenant,
            workspace=plan.identity.workspace,
            site=plan.identity.site,
            mount=plan.identity.mount,
            node_id=plan.identity.node_id,
            paths=plan.changeset.paths,
            environments=tuple(
                output.environment
                for output in plan.intended_outputs
                if output.environment is not None
            ),
            resulting_visibility=plan.request.resulting_visibility,
        )

    def same_resource(self, other: PublicationApprovalScope) -> bool:
        return (
            self.tenant,
            self.workspace,
            self.site,
            self.mount,
            self.node_id,
        ) == (
            other.tenant,
            other.workspace,
            other.site,
            other.mount,
            other.node_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "tenant": self.tenant,
            "workspace": self.workspace,
            "site": self.site,
            "mount": self.mount,
            "node_id": self.node_id,
            "paths": list(self.paths),
            "environments": list(self.environments),
            "resulting_visibility": self.resulting_visibility,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationApprovalScope:
        return cls(
            tenant=str(value.get("tenant") or ""),
            workspace=str(value.get("workspace") or ""),
            site=str(value.get("site") or ""),
            mount=str(value.get("mount") or ""),
            node_id=str(value.get("node_id") or ""),
            paths=_string_tuple(value.get("paths")),
            environments=_string_tuple(value.get("environments")),
            resulting_visibility=str(value.get("resulting_visibility") or ""),
        )


@dataclass(frozen=True, slots=True)
class PublicationApprovalRecord:
    record_id: str
    record_digest: str
    idempotency_key: str
    input_digest: str
    policy_version: str
    correlation_id: str
    scope: PublicationApprovalScope
    decision: PublicationDecision
    target_record_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", _required(self.record_id, "record_id"))
        _digest(self.record_digest)
        object.__setattr__(
            self, "idempotency_key", _required(self.idempotency_key, "idempotency_key")
        )
        _digest(self.input_digest)
        object.__setattr__(self, "policy_version", _required(self.policy_version, "policy_version"))
        object.__setattr__(self, "correlation_id", _required(self.correlation_id, "correlation_id"))
        targets = _sorted_strings(self.target_record_ids)
        object.__setattr__(self, "target_record_ids", targets)
        if self.decision.decision in _CONTROL_DECISIONS and not targets:
            raise ValueError("control publication decision requires target record IDs")
        if self.decision.decision not in _CONTROL_DECISIONS and targets:
            raise ValueError("only control publication decisions may name target records")
        expected = sha256_digest(canonical_json_bytes(self.digest_payload()))
        if self.record_digest != expected:
            raise ValueError("publication approval record_digest does not match payload")
        if self.record_id != f"approval-{expected.removeprefix('sha256:')[:24]}":
            raise ValueError("publication approval record_id does not match digest")

    @classmethod
    def create(
        cls,
        *,
        idempotency_key: str,
        input_digest: str,
        policy_version: str,
        correlation_id: str,
        scope: PublicationApprovalScope,
        decision: PublicationDecision,
        target_record_ids: tuple[str, ...] = (),
    ) -> PublicationApprovalRecord:
        payload = _record_digest_payload(
            idempotency_key=_required(idempotency_key, "idempotency_key"),
            input_digest=_validated_digest(input_digest),
            policy_version=_required(policy_version, "policy_version"),
            correlation_id=_required(correlation_id, "correlation_id"),
            scope=scope,
            decision=decision,
            target_record_ids=_sorted_strings(target_record_ids),
        )
        digest = sha256_digest(canonical_json_bytes(payload))
        return cls(
            record_id=f"approval-{digest.removeprefix('sha256:')[:24]}",
            record_digest=digest,
            idempotency_key=idempotency_key,
            input_digest=input_digest,
            policy_version=policy_version,
            correlation_id=correlation_id,
            scope=scope,
            decision=decision,
            target_record_ids=target_record_ids,
        )

    def digest_payload(self) -> dict[str, object]:
        return _record_digest_payload(
            idempotency_key=self.idempotency_key,
            input_digest=self.input_digest,
            policy_version=self.policy_version,
            correlation_id=self.correlation_id,
            scope=self.scope,
            decision=self.decision,
            target_record_ids=self.target_record_ids,
        )

    def to_dict(self, projection: ApprovalProjection = "trusted") -> dict[str, object]:
        _projection(projection)
        payload: dict[str, object] = {
            "schema_version": PUBLICATION_APPROVAL_SCHEMA_VERSION,
            "record_type": "furatena.publication.approval-record",
            "record_id": self.record_id,
            "record_digest": self.record_digest,
            "policy_version": self.policy_version,
            "scope": (
                self.scope.to_dict()
                if projection != "public"
                else {
                    "site": self.scope.site,
                    "mount": self.scope.mount,
                    "path_count": len(self.scope.paths),
                    "environments": list(self.scope.environments),
                    "resulting_visibility": self.scope.resulting_visibility,
                }
            ),
            "decision": self.decision.to_dict(projection),
            "target_record_ids": list(self.target_record_ids),
        }
        if projection != "public":
            payload["correlation_id"] = self.correlation_id
        if projection == "trusted":
            payload["idempotency_key"] = self.idempotency_key
            payload["input_digest"] = self.input_digest
        elif projection == "audit":
            payload["input_digest"] = self.input_digest
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationApprovalRecord:
        _record_header(value, "furatena.publication.approval-record")
        return cls(
            record_id=str(value.get("record_id") or ""),
            record_digest=str(value.get("record_digest") or ""),
            idempotency_key=str(value.get("idempotency_key") or ""),
            input_digest=str(value.get("input_digest") or ""),
            policy_version=str(value.get("policy_version") or ""),
            correlation_id=str(value.get("correlation_id") or ""),
            scope=PublicationApprovalScope.from_dict(_mapping(value.get("scope"), "scope")),
            decision=PublicationDecision.from_dict(_mapping(value.get("decision"), "decision")),
            target_record_ids=_string_tuple(value.get("target_record_ids")),
        )


@dataclass(frozen=True, slots=True)
class PublicationApprovalEvaluation:
    plan_digest: str
    policy_version: str
    policy_digest: str
    satisfied: bool
    override_applied: bool
    required_count: int
    approval_record_ids: tuple[str, ...]
    waiver_record_ids: tuple[str, ...]
    blocking_record_ids: tuple[str, ...]
    invalidated_record_ids: tuple[str, ...]
    unwaived_warning_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    evaluated_at: str

    def __post_init__(self) -> None:
        _digest(self.plan_digest)
        _digest(self.policy_digest)
        object.__setattr__(self, "policy_version", _required(self.policy_version, "policy_version"))
        if self.required_count < 0:
            raise ValueError("approval evaluation required_count must be non-negative")
        for name in (
            "approval_record_ids",
            "waiver_record_ids",
            "blocking_record_ids",
            "invalidated_record_ids",
            "unwaived_warning_ids",
            "reason_codes",
        ):
            object.__setattr__(self, name, _sorted_strings(getattr(self, name)))
        object.__setattr__(self, "evaluated_at", normalize_rfc3339(self.evaluated_at))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PUBLICATION_APPROVAL_SCHEMA_VERSION,
            "record_type": "furatena.publication.approval-evaluation",
            "plan_digest": self.plan_digest,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "satisfied": self.satisfied,
            "override_applied": self.override_applied,
            "required_count": self.required_count,
            "approval_record_ids": list(self.approval_record_ids),
            "waiver_record_ids": list(self.waiver_record_ids),
            "blocking_record_ids": list(self.blocking_record_ids),
            "invalidated_record_ids": list(self.invalidated_record_ids),
            "unwaived_warning_ids": list(self.unwaived_warning_ids),
            "reason_codes": list(self.reason_codes),
            "evaluated_at": self.evaluated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationApprovalEvaluation:
        _record_header(value, "furatena.publication.approval-evaluation")
        return cls(
            plan_digest=str(value.get("plan_digest") or ""),
            policy_version=str(value.get("policy_version") or ""),
            policy_digest=str(value.get("policy_digest") or ""),
            satisfied=bool(value.get("satisfied")),
            override_applied=bool(value.get("override_applied")),
            required_count=int(value.get("required_count") or 0),
            approval_record_ids=_string_tuple(value.get("approval_record_ids")),
            waiver_record_ids=_string_tuple(value.get("waiver_record_ids")),
            blocking_record_ids=_string_tuple(value.get("blocking_record_ids")),
            invalidated_record_ids=_string_tuple(value.get("invalidated_record_ids")),
            unwaived_warning_ids=_string_tuple(value.get("unwaived_warning_ids")),
            reason_codes=_string_tuple(value.get("reason_codes")),
            evaluated_at=str(value.get("evaluated_at") or ""),
        )


class PublicationApprovalStore(Protocol):
    def append(self, record: PublicationApprovalRecord) -> PublicationApprovalRecord: ...

    def get(self, record_id: str) -> PublicationApprovalRecord: ...

    def get_by_idempotency(self, idempotency_key: str) -> PublicationApprovalRecord | None: ...

    def list_records(self) -> tuple[PublicationApprovalRecord, ...]: ...


class InMemoryPublicationApprovalStore:
    def __init__(self) -> None:
        self._records: dict[str, PublicationApprovalRecord] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = threading.RLock()

    def append(self, record: PublicationApprovalRecord) -> PublicationApprovalRecord:
        with self._lock:
            existing_id = self._idempotency.get(record.idempotency_key)
            if existing_id is not None:
                existing = self._records[existing_id]
                _same_input_or_conflict(existing, record.input_digest)
                return existing
            existing = self._records.get(record.record_id)
            if existing is not None:
                if existing == record:
                    return existing
                _fail(PublicationApprovalErrorCode.CORRUPT, "approval record ID collision")
            self._records[record.record_id] = record
            self._idempotency[record.idempotency_key] = record.record_id
            return record

    def get(self, record_id: str) -> PublicationApprovalRecord:
        with self._lock:
            try:
                return self._records[record_id]
            except KeyError:
                _fail(PublicationApprovalErrorCode.NOT_FOUND, "approval record not found")

    def get_by_idempotency(self, idempotency_key: str) -> PublicationApprovalRecord | None:
        with self._lock:
            record_id = self._idempotency.get(idempotency_key)
            return self._records.get(record_id) if record_id else None

    def list_records(self) -> tuple[PublicationApprovalRecord, ...]:
        with self._lock:
            return tuple(sorted(self._records.values(), key=_record_order))


class JsonDirectoryPublicationApprovalStore:
    """Restart-safe append-only approval records with atomic idempotency refs."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.records = self.root / "records"
        self.idempotency = self.root / "idempotency"
        for path in (self.root, self.records, self.idempotency):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)
        self._lock = threading.RLock()

    def append(self, record: PublicationApprovalRecord) -> PublicationApprovalRecord:
        with self._lock:
            existing = self.get_by_idempotency(record.idempotency_key)
            if existing is not None:
                _same_input_or_conflict(existing, record.input_digest)
                self._materialize(existing)
                return existing
            ref_path = self.idempotency / f"{_safe(record.idempotency_key)}.json"
            try:
                _write_new(ref_path, record.to_dict())
            except FileExistsError:
                replay = self.get_by_idempotency(record.idempotency_key)
                if replay is None:
                    _fail(PublicationApprovalErrorCode.CORRUPT, "invalid idempotency reference")
                _same_input_or_conflict(replay, record.input_digest)
                self._materialize(replay)
                return replay
            self._materialize(record)
            return record

    def get(self, record_id: str) -> PublicationApprovalRecord:
        value = _read_json(self.records / f"{_safe(record_id)}.json", "approval record")
        try:
            record = PublicationApprovalRecord.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            _fail(PublicationApprovalErrorCode.CORRUPT, f"invalid approval record: {exc}")
        if record.record_id != record_id:
            _fail(PublicationApprovalErrorCode.CORRUPT, "approval record identity mismatch")
        return record

    def get_by_idempotency(self, idempotency_key: str) -> PublicationApprovalRecord | None:
        path = self.idempotency / f"{_safe(idempotency_key)}.json"
        if not path.exists():
            return None
        value = _read_json(path, "approval idempotency reference")
        try:
            record = PublicationApprovalRecord.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            _fail(PublicationApprovalErrorCode.CORRUPT, f"invalid idempotency record: {exc}")
        if record.idempotency_key != idempotency_key:
            _fail(PublicationApprovalErrorCode.CORRUPT, "idempotency identity mismatch")
        return record

    def list_records(self) -> tuple[PublicationApprovalRecord, ...]:
        return tuple(
            sorted(
                (self.get(path.stem) for path in self.records.glob("*.json")),
                key=_record_order,
            )
        )

    def _materialize(self, record: PublicationApprovalRecord) -> None:
        record_path = self.records / f"{_safe(record.record_id)}.json"
        try:
            _write_new(record_path, record.to_dict())
        except FileExistsError:
            persisted = self.get(record.record_id)
            if persisted != record:
                _fail(PublicationApprovalErrorCode.CORRUPT, "approval record ID collision")


class PublicationApprovalService:
    def __init__(
        self,
        *,
        store: PublicationApprovalStore,
        audit_store: AuditStore,
        authorizer: Callable[[PublicationPlan, PublicationActor, PublicationDecisionKind], bool],
        clock: Callable[[], str],
    ) -> None:
        self.store = store
        self.audit_store = audit_store
        self.authorizer = authorizer
        self.clock = clock

    def decide(
        self,
        plan: PublicationPlan,
        *,
        actor: PublicationActor,
        decision: PublicationDecisionKind,
        idempotency_key: str,
        reason: str = "",
        diagnostic_ids: tuple[str, ...] = (),
        expires_at: str | None = None,
        target_record_ids: tuple[str, ...] = (),
    ) -> PublicationApprovalRecord:
        decision = PublicationDecisionKind(decision)
        timestamp = self.clock()
        scope = PublicationApprovalScope.from_plan(plan)
        input_payload = {
            "plan_digest": plan.plan_digest,
            "policy_version": plan.bindings.policy_version,
            "policy_digest": plan.bindings.policy_digest,
            "actor": actor.to_dict(),
            "decision": decision.value,
            "reason": str(reason or "").strip(),
            "diagnostic_ids": list(_sorted_strings(diagnostic_ids)),
            "expires_at": normalize_rfc3339(expires_at) if expires_at else None,
            "target_record_ids": list(_sorted_strings(target_record_ids)),
            "scope": scope.to_dict(),
        }
        input_digest = sha256_digest(canonical_json_bytes(input_payload))
        existing = self.store.get_by_idempotency(idempotency_key)
        if existing is not None:
            _same_input_or_conflict(existing, input_digest)
            return existing
        self._authorize(plan, actor, decision)
        self._validate_command(
            plan,
            actor,
            decision,
            diagnostic_ids=diagnostic_ids,
            expires_at=expires_at,
            target_record_ids=target_record_ids,
        )
        core = PublicationDecision.create(
            plan_digest=plan.plan_digest,
            policy_digest=plan.bindings.policy_digest,
            actor=actor,
            decision=decision,
            reason=reason,
            diagnostic_ids=diagnostic_ids,
            timestamp=timestamp,
            expires_at=expires_at,
        )
        record = PublicationApprovalRecord.create(
            idempotency_key=idempotency_key,
            input_digest=input_digest,
            policy_version=plan.bindings.policy_version,
            correlation_id=plan.correlation_id,
            scope=scope,
            decision=core,
            target_record_ids=target_record_ids,
        )
        persisted = self.store.append(record)
        self._audit(plan, persisted, "accepted")
        return persisted

    def evaluate(
        self, plan: PublicationPlan, *, now: str | None = None
    ) -> PublicationApprovalEvaluation:
        evaluated_at = normalize_rfc3339(now or self.clock())
        current_scope = PublicationApprovalScope.from_plan(plan)
        candidates = tuple(
            record
            for record in self.store.list_records()
            if record.scope.same_resource(current_scope)
        )
        invalid: set[str] = set()
        active: dict[str, PublicationApprovalRecord] = {}
        for record in candidates:
            if not self._current(plan, current_scope, record, evaluated_at):
                invalid.add(record.record_id)
            else:
                active[record.record_id] = record
        invalid.update(self._controlled(active))
        effective = {
            record_id: record
            for record_id, record in active.items()
            if record_id not in invalid and record.decision.decision not in _CONTROL_DECISIONS
        }
        overrides = tuple(
            record
            for record in effective.values()
            if record.decision.decision == PublicationDecisionKind.EMERGENCY_OVERRIDE
        )
        blockers = tuple(
            record
            for record in effective.values()
            if record.decision.decision
            in {PublicationDecisionKind.REJECT, PublicationDecisionKind.REQUEST_CHANGES}
        )
        approvals = self._eligible_approvals(plan, effective.values())
        waivers = tuple(
            record
            for record in effective.values()
            if record.decision.decision == PublicationDecisionKind.WAIVE_WARNING
        )
        waived = {
            diagnostic
            for record in waivers
            for diagnostic in record.decision.diagnostic_ids
            if diagnostic in plan.validation.waivable_warning_ids
        }
        unwaived = set(plan.validation.waivable_warning_ids) - waived
        override_applied = bool(overrides)
        satisfied = override_applied or (
            len(approvals) >= plan.approval_requirements.required_count
            and not blockers
            and not unwaived
        )
        reasons: set[str] = set()
        if len(approvals) < plan.approval_requirements.required_count:
            reasons.add("insufficient_approvals")
        if blockers:
            reasons.add("blocking_decision")
        if unwaived:
            reasons.add("unwaived_warnings")
        if invalid:
            reasons.add("invalidated_records")
        if override_applied:
            reasons = {"emergency_override"}
        if satisfied and not reasons:
            reasons.add("requirements_satisfied")
        evaluation = PublicationApprovalEvaluation(
            plan_digest=plan.plan_digest,
            policy_version=plan.bindings.policy_version,
            policy_digest=plan.bindings.policy_digest,
            satisfied=satisfied,
            override_applied=override_applied,
            required_count=plan.approval_requirements.required_count,
            approval_record_ids=tuple(record.record_id for record in approvals),
            waiver_record_ids=tuple(record.record_id for record in waivers),
            blocking_record_ids=tuple(record.record_id for record in blockers),
            invalidated_record_ids=tuple(invalid),
            unwaived_warning_ids=tuple(unwaived),
            reason_codes=tuple(reasons),
            evaluated_at=evaluated_at,
        )
        self._audit_evaluation(plan, evaluation)
        return evaluation

    def _authorize(
        self,
        plan: PublicationPlan,
        actor: PublicationActor,
        decision: PublicationDecisionKind,
    ) -> None:
        if not self.authorizer(plan, actor, decision):
            raise PublicationApprovalError(
                PublicationApprovalErrorCode.AUTHORIZATION,
                f"actor is not authorized to {decision.value}",
                "Use a currently authorized trusted identity.",
            )

    def _validate_command(
        self,
        plan: PublicationPlan,
        actor: PublicationActor,
        decision: PublicationDecisionKind,
        *,
        diagnostic_ids: tuple[str, ...],
        expires_at: str | None,
        target_record_ids: tuple[str, ...],
    ) -> None:
        if decision == PublicationDecisionKind.APPROVE:
            if not _eligible(plan, actor):
                raise PublicationApprovalError(
                    PublicationApprovalErrorCode.INELIGIBLE,
                    "actor is not eligible to approve this publication scope",
                    "Use an actor with an eligible role or team.",
                )
            if not plan.approval_requirements.allow_self_approval and _same_actor(
                actor, plan.creator
            ):
                raise PublicationApprovalError(
                    PublicationApprovalErrorCode.SELF_APPROVAL,
                    "publication policy does not allow self approval",
                    "Request approval from a distinct eligible actor.",
                )
        if decision == PublicationDecisionKind.WAIVE_WARNING:
            requested = set(diagnostic_ids)
            allowed = set(plan.validation.waivable_warning_ids)
            if not requested or not requested <= allowed or expires_at is None:
                raise PublicationApprovalError(
                    PublicationApprovalErrorCode.INVALID_WAIVER,
                    "warning waiver must name only explicitly waivable diagnostics and expire",
                    "Supply a non-empty listed warning set, reason, and expiry.",
                )
            if _timestamp(expires_at) > _timestamp(plan.expires_at):
                raise PublicationApprovalError(
                    PublicationApprovalErrorCode.INVALID_WAIVER,
                    "warning waiver cannot outlive its publication plan",
                    "Choose an expiry at or before the plan expiry.",
                )
        if decision in _CONTROL_DECISIONS:
            if not target_record_ids:
                raise PublicationApprovalError(
                    PublicationApprovalErrorCode.INVALID_TARGET,
                    "control decision requires at least one target record",
                    "Name an active record from this publication plan.",
                )
            for record_id in target_record_ids:
                target = self.store.get(record_id)
                if (
                    target.decision.plan_digest != plan.plan_digest
                    or target.decision.decision in _CONTROL_DECISIONS
                ):
                    raise PublicationApprovalError(
                        PublicationApprovalErrorCode.INVALID_TARGET,
                        "control decision target is not an eligible record from this plan",
                        "Target a non-control decision bound to this plan digest.",
                    )

    def _current(
        self,
        plan: PublicationPlan,
        scope: PublicationApprovalScope,
        record: PublicationApprovalRecord,
        now: str,
    ) -> bool:
        decision = record.decision
        current_capability = decision.decision not in {
            PublicationDecisionKind.APPROVE,
            PublicationDecisionKind.WAIVE_WARNING,
            PublicationDecisionKind.EMERGENCY_OVERRIDE,
        } or self.authorizer(plan, decision.actor, decision.decision)
        return bool(
            decision.plan_digest == plan.plan_digest
            and decision.policy_digest == plan.bindings.policy_digest
            and record.policy_version == plan.bindings.policy_version
            and record.scope == scope
            and _timestamp(decision.timestamp) <= _timestamp(now)
            and (decision.expires_at is None or _timestamp(decision.expires_at) > _timestamp(now))
            and _eligible_record(plan, record)
            and current_capability
        )

    @staticmethod
    def _controlled(active: Mapping[str, PublicationApprovalRecord]) -> set[str]:
        invalid: set[str] = set()
        for record in active.values():
            if record.decision.decision in _CONTROL_DECISIONS:
                invalid.update(target for target in record.target_record_ids if target in active)
        return invalid

    @staticmethod
    def _eligible_approvals(
        plan: PublicationPlan,
        records: Any,
    ) -> tuple[PublicationApprovalRecord, ...]:
        approvals: list[PublicationApprovalRecord] = []
        actors: set[tuple[str, str]] = set()
        for record in sorted(records, key=_record_order):
            if record.decision.decision != PublicationDecisionKind.APPROVE:
                continue
            actor = record.decision.actor
            identity = (actor.actor, actor.identity_source)
            if identity in actors or not _eligible(plan, actor):
                continue
            if not plan.approval_requirements.allow_self_approval and _same_actor(
                actor, plan.creator
            ):
                continue
            actors.add(identity)
            approvals.append(record)
        return tuple(approvals)

    def _audit(
        self, plan: PublicationPlan, record: PublicationApprovalRecord, outcome: str
    ) -> None:
        self.audit_store.append(
            {
                "event_id": record.record_id,
                "correlation_id": record.correlation_id,
                "actor": record.decision.actor.actor,
                "tenant": plan.identity.tenant,
                "site": plan.identity.site,
                "action": f"publication.decision.{record.decision.decision.value}",
                "target": plan.plan_id,
                "outcome": outcome,
                "record_digest": record.record_digest,
                "policy_digest": record.decision.policy_digest,
            }
        )

    def _audit_evaluation(
        self, plan: PublicationPlan, evaluation: PublicationApprovalEvaluation
    ) -> None:
        self.audit_store.append(
            {
                "correlation_id": plan.correlation_id,
                "actor": "publication-workflow",
                "tenant": plan.identity.tenant,
                "site": plan.identity.site,
                "action": "publication.approval.evaluate",
                "target": plan.plan_id,
                "outcome": "allowed" if evaluation.satisfied else "denied",
                "policy_digest": evaluation.policy_digest,
                "reason_codes": list(evaluation.reason_codes),
            }
        )


@dataclass(frozen=True, slots=True)
class PublicationApprovalTransportAdapter:
    transport: Literal["browser", "cli", "mcp"]
    service: PublicationApprovalService

    def decide(
        self,
        plan: PublicationPlan,
        command: Mapping[str, Any],
        *,
        trusted_actor: PublicationActor,
    ) -> dict[str, object]:
        record = self.service.decide(
            plan,
            actor=trusted_actor,
            decision=PublicationDecisionKind(str(command["decision"])),
            idempotency_key=str(command["idempotency_key"]),
            reason=str(command.get("reason") or ""),
            diagnostic_ids=_string_tuple(command.get("diagnostic_ids")),
            expires_at=(
                str(command["expires_at"]) if command.get("expires_at") is not None else None
            ),
            target_record_ids=_string_tuple(command.get("target_record_ids")),
        )
        return record.to_dict()


def _record_digest_payload(
    *,
    idempotency_key: str,
    input_digest: str,
    policy_version: str,
    correlation_id: str,
    scope: PublicationApprovalScope,
    decision: PublicationDecision,
    target_record_ids: tuple[str, ...],
) -> dict[str, object]:
    return {
        "idempotency_key": idempotency_key,
        "input_digest": input_digest,
        "policy_version": policy_version,
        "correlation_id": correlation_id,
        "scope": scope.to_dict(),
        "decision": decision.to_dict(),
        "target_record_ids": list(target_record_ids),
    }


def _eligible(plan: PublicationPlan, actor: PublicationActor) -> bool:
    requirements = plan.approval_requirements
    roles = set(actor.roles)
    teams = set(actor.teams)
    role_ok = not requirements.eligible_roles or bool(roles & set(requirements.eligible_roles))
    team_ok = not requirements.eligible_teams or bool(teams & set(requirements.eligible_teams))
    if requirements.eligible_roles and requirements.eligible_teams:
        return role_ok or team_ok
    return role_ok and team_ok


def _eligible_record(plan: PublicationPlan, record: PublicationApprovalRecord) -> bool:
    kind = record.decision.decision
    if kind == PublicationDecisionKind.APPROVE:
        return _eligible(plan, record.decision.actor)
    if kind == PublicationDecisionKind.WAIVE_WARNING:
        return bool(
            record.decision.diagnostic_ids
            and set(record.decision.diagnostic_ids) <= set(plan.validation.waivable_warning_ids)
        )
    return True


def _same_actor(left: PublicationActor, right: PublicationActor) -> bool:
    return (left.actor, left.identity_source) == (right.actor, right.identity_source)


def _same_input_or_conflict(record: PublicationApprovalRecord, input_digest: str) -> None:
    if record.input_digest != input_digest:
        raise PublicationApprovalError(
            PublicationApprovalErrorCode.IDEMPOTENCY_CONFLICT,
            "idempotency key was already used for different approval input",
            "Use the original input or a new idempotency key.",
        )


def _record_order(record: PublicationApprovalRecord) -> tuple[str, str]:
    return (record.decision.timestamp, record.record_id)


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(normalize_rfc3339(value).replace("Z", "+00:00"))


def _required(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"publication approval {label} is required")
    return normalized


def _sorted_strings(values: Any) -> tuple[str, ...]:
    return tuple(sorted({_required(value, "list value") for value in values or ()}))


def _sorted_required(values: Any, label: str) -> tuple[str, ...]:
    result = _sorted_strings(values)
    if not result:
        raise ValueError(f"publication approval {label} must not be empty")
    return result


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError("publication approval list must be an array")
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError("publication approval list must be an array")
    return tuple(str(item) for item in value)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"publication approval {label} must be an object")
    return cast(Mapping[str, Any], value)


def _projection(value: str) -> None:
    if value not in {"trusted", "audit", "public"}:
        raise ValueError(f"unsupported approval projection: {value}")


def _record_header(value: Mapping[str, Any], record_type: str) -> None:
    if value.get("schema_version") != PUBLICATION_APPROVAL_SCHEMA_VERSION:
        raise ValueError("unsupported publication approval schema version")
    if value.get("record_type") != record_type:
        raise ValueError("invalid publication approval record type")


def _digest(value: str) -> None:
    if not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"invalid publication approval digest: {value!r}")


def _validated_digest(value: str) -> str:
    _digest(value)
    return value


def _safe(value: str) -> str:
    if not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"unsafe publication approval identifier: {value!r}")
    return value


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail(PublicationApprovalErrorCode.NOT_FOUND, f"{label} not found")
    except (OSError, json.JSONDecodeError) as exc:
        _fail(PublicationApprovalErrorCode.CORRUPT, f"invalid {label}: {exc}")
    if not isinstance(value, Mapping):
        _fail(PublicationApprovalErrorCode.CORRUPT, f"{label} must be an object")
    return value


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    payload = canonical_json_bytes(value) + b"\n"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("publication approval write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _fail(code: str, message: str) -> Never:
    raise PublicationApprovalError(
        code,
        message,
        "Inspect the durable approval store and retry with current plan state.",
    )
