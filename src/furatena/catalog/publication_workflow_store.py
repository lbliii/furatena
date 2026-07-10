"""Operational persistence for publication workflow orchestration."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Never, Protocol

from furatena.catalog.publication_contracts import (
    PublicationEvent,
    PublicationPlan,
    PublicationStateSnapshot,
    canonical_json_bytes,
    normalize_rfc3339,
    sha256_digest,
)

WORKFLOW_STORE_SCHEMA_VERSION = 1
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


class PublicationWorkflowStoreErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    STALE = "stale_state"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    RECEIPT_COMPLETED = "receipt_completed"
    CORRUPT = "corrupt_record"


class PublicationWorkflowStoreError(RuntimeError):
    """Typed fail-closed operational-store error."""

    def __init__(self, code: PublicationWorkflowStoreErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class PublicationOperationStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


@dataclass(frozen=True, slots=True)
class PublicationOperationReceipt:
    """Durable idempotency record written before a workflow side effect."""

    idempotency_key: str
    command: str
    plan_id: str
    plan_digest: str
    expected_state_version: int
    input_digest: str
    status: PublicationOperationStatus
    started_at: str
    completed_at: str | None = None
    response: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in ("idempotency_key", "command", "plan_id"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"publication operation {name} is required")
            object.__setattr__(self, name, value)
        _digest(self.plan_digest)
        _digest(self.input_digest)
        if self.expected_state_version < 1:
            raise ValueError("publication operation expected_state_version must be positive")
        object.__setattr__(self, "status", PublicationOperationStatus(self.status))
        object.__setattr__(self, "started_at", normalize_rfc3339(self.started_at))
        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", normalize_rfc3339(self.completed_at))
        if self.status == PublicationOperationStatus.STARTED:
            if self.completed_at is not None or self.response is not None:
                raise ValueError("started publication operation cannot contain a result")
        elif self.completed_at is None or self.response is None:
            raise ValueError("completed publication operation requires time and response")
        response = None if self.response is None else dict(self.response)
        canonical_json_bytes(response)
        object.__setattr__(self, "response", response)

    @classmethod
    def start(
        cls,
        *,
        idempotency_key: str,
        command: str,
        plan_id: str,
        plan_digest: str,
        expected_state_version: int,
        input_payload: object,
        started_at: str,
    ) -> PublicationOperationReceipt:
        return cls(
            idempotency_key=idempotency_key,
            command=command,
            plan_id=plan_id,
            plan_digest=plan_digest,
            expected_state_version=expected_state_version,
            input_digest=sha256_digest(canonical_json_bytes(input_payload)),
            status=PublicationOperationStatus.STARTED,
            started_at=started_at,
        )

    def complete(
        self,
        *,
        status: PublicationOperationStatus,
        completed_at: str,
        response: Mapping[str, Any],
    ) -> PublicationOperationReceipt:
        status = PublicationOperationStatus(status)
        if status == PublicationOperationStatus.STARTED:
            raise ValueError("completion status cannot be started")
        return replace(self, status=status, completed_at=completed_at, response=response)

    def same_operation(self, other: PublicationOperationReceipt) -> bool:
        return (
            self.idempotency_key == other.idempotency_key
            and self.command == other.command
            and self.plan_id == other.plan_id
            and self.plan_digest == other.plan_digest
            and self.expected_state_version == other.expected_state_version
            and self.input_digest == other.input_digest
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": WORKFLOW_STORE_SCHEMA_VERSION,
            "record_type": "furatena.publication.operation-receipt",
            "idempotency_key": self.idempotency_key,
            "command": self.command,
            "plan_id": self.plan_id,
            "plan_digest": self.plan_digest,
            "expected_state_version": self.expected_state_version,
            "input_digest": self.input_digest,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "response": self.response,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationOperationReceipt:
        if value.get("schema_version") != WORKFLOW_STORE_SCHEMA_VERSION:
            raise ValueError("unsupported publication operation receipt schema version")
        if value.get("record_type") != "furatena.publication.operation-receipt":
            raise ValueError("invalid publication operation receipt record type")
        response = value.get("response")
        if response is not None and not isinstance(response, Mapping):
            raise ValueError("publication operation response must be an object")
        return cls(
            idempotency_key=str(value.get("idempotency_key") or ""),
            command=str(value.get("command") or ""),
            plan_id=str(value.get("plan_id") or ""),
            plan_digest=str(value.get("plan_digest") or ""),
            expected_state_version=int(value.get("expected_state_version") or 0),
            input_digest=str(value.get("input_digest") or ""),
            status=PublicationOperationStatus(str(value.get("status") or "")),
            started_at=str(value.get("started_at") or ""),
            completed_at=(
                str(value["completed_at"]) if value.get("completed_at") is not None else None
            ),
            response=response,
        )


class PublicationWorkflowStore(Protocol):
    def create_plan(
        self, plan: PublicationPlan, initial_snapshot: PublicationStateSnapshot
    ) -> None: ...

    def get_plan(self, plan_id: str) -> PublicationPlan: ...

    def get_snapshot(self, plan_id: str) -> PublicationStateSnapshot: ...

    def compare_and_swap(
        self,
        plan_id: str,
        expected_state_version: int,
        snapshot: PublicationStateSnapshot,
        event: PublicationEvent,
    ) -> None: ...

    def get_events(
        self, plan_id: str, *, after_state_version: int = 0
    ) -> tuple[PublicationEvent, ...]: ...

    def begin_operation(
        self, receipt: PublicationOperationReceipt
    ) -> PublicationOperationReceipt: ...

    def complete_operation(
        self, receipt: PublicationOperationReceipt
    ) -> PublicationOperationReceipt: ...

    def get_operation(self, idempotency_key: str) -> PublicationOperationReceipt | None: ...


class InMemoryPublicationWorkflowStore:
    """Thread-safe operational store for embedding and tests."""

    def __init__(self) -> None:
        self._plans: dict[str, PublicationPlan] = {}
        self._snapshots: dict[str, PublicationStateSnapshot] = {}
        self._events: dict[str, list[PublicationEvent]] = {}
        self._receipts: dict[str, PublicationOperationReceipt] = {}
        self._lock = threading.RLock()

    def create_plan(
        self, plan: PublicationPlan, initial_snapshot: PublicationStateSnapshot
    ) -> None:
        _validate_initial(plan, initial_snapshot)
        with self._lock:
            existing = self._plans.get(plan.plan_id)
            if existing is not None:
                if existing.plan_digest == plan.plan_digest:
                    return
                _raise(PublicationWorkflowStoreErrorCode.ALREADY_EXISTS, "plan ID already exists")
            self._plans[plan.plan_id] = plan
            self._snapshots[plan.plan_id] = initial_snapshot
            self._events[plan.plan_id] = []

    def get_plan(self, plan_id: str) -> PublicationPlan:
        with self._lock:
            try:
                return self._plans[plan_id]
            except KeyError:
                _raise(PublicationWorkflowStoreErrorCode.NOT_FOUND, "publication plan not found")

    def get_snapshot(self, plan_id: str) -> PublicationStateSnapshot:
        with self._lock:
            try:
                return self._snapshots[plan_id]
            except KeyError:
                _raise(
                    PublicationWorkflowStoreErrorCode.NOT_FOUND, "publication snapshot not found"
                )

    def compare_and_swap(
        self,
        plan_id: str,
        expected_state_version: int,
        snapshot: PublicationStateSnapshot,
        event: PublicationEvent,
    ) -> None:
        with self._lock:
            current = self.get_snapshot(plan_id)
            _validate_cas(plan_id, current, expected_state_version, snapshot, event)
            self._snapshots[plan_id] = snapshot
            self._events[plan_id].append(event)

    def get_events(
        self, plan_id: str, *, after_state_version: int = 0
    ) -> tuple[PublicationEvent, ...]:
        with self._lock:
            if plan_id not in self._plans:
                _raise(PublicationWorkflowStoreErrorCode.NOT_FOUND, "publication plan not found")
            return tuple(
                event
                for event in self._events[plan_id]
                if event.state_version > after_state_version
            )

    def begin_operation(self, receipt: PublicationOperationReceipt) -> PublicationOperationReceipt:
        if receipt.status != PublicationOperationStatus.STARTED:
            raise ValueError("begin_operation requires a started receipt")
        with self._lock:
            existing = self._receipts.get(receipt.idempotency_key)
            if existing is not None:
                _same_receipt_or_conflict(existing, receipt)
                return existing
            self._receipts[receipt.idempotency_key] = receipt
            return receipt

    def complete_operation(
        self, receipt: PublicationOperationReceipt
    ) -> PublicationOperationReceipt:
        if receipt.status == PublicationOperationStatus.STARTED:
            raise ValueError("complete_operation requires a completed receipt")
        with self._lock:
            existing = self._receipts.get(receipt.idempotency_key)
            if existing is None:
                _raise(PublicationWorkflowStoreErrorCode.NOT_FOUND, "operation receipt not found")
            _same_receipt_or_conflict(existing, receipt)
            if existing.status != PublicationOperationStatus.STARTED:
                if existing == receipt:
                    return existing
                _raise(
                    PublicationWorkflowStoreErrorCode.RECEIPT_COMPLETED,
                    "operation receipt is already completed",
                )
            self._receipts[receipt.idempotency_key] = receipt
            return receipt

    def get_operation(self, idempotency_key: str) -> PublicationOperationReceipt | None:
        with self._lock:
            return self._receipts.get(idempotency_key)


class JsonDirectoryPublicationWorkflowStore:
    """Restart-safe JSON-directory operational store with fail-closed reads."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.plans = self.root / "plans"
        self.snapshots = self.root / "snapshots"
        self.events = self.root / "events"
        self.receipts = self.root / "receipts"
        for directory in (self.root, self.plans, self.snapshots, self.events, self.receipts):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
        self._lock = threading.RLock()

    def create_plan(
        self, plan: PublicationPlan, initial_snapshot: PublicationStateSnapshot
    ) -> None:
        _validate_initial(plan, initial_snapshot)
        with self._lock:
            path = self._path(self.plans, plan.plan_id)
            if path.exists():
                existing = self.get_plan(plan.plan_id)
                if existing.plan_digest == plan.plan_digest:
                    return
                _raise(PublicationWorkflowStoreErrorCode.ALREADY_EXISTS, "plan ID already exists")
            _write_new(path, plan.to_dict())
            try:
                _write_new(self._path(self.snapshots, plan.plan_id), initial_snapshot.to_dict())
                (self.events / _safe(plan.plan_id)).mkdir(mode=0o700)
            except BaseException:
                path.unlink(missing_ok=True)
                self._path(self.snapshots, plan.plan_id).unlink(missing_ok=True)
                raise

    def get_plan(self, plan_id: str) -> PublicationPlan:
        value = self._read(self._path(self.plans, plan_id), "publication plan")
        try:
            plan = PublicationPlan.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            _corrupt("publication plan", exc)
        if plan.plan_id != plan_id:
            _raise(PublicationWorkflowStoreErrorCode.CORRUPT, "plan file identity mismatch")
        return plan

    def get_snapshot(self, plan_id: str) -> PublicationStateSnapshot:
        value = self._read(self._path(self.snapshots, plan_id), "publication snapshot")
        try:
            snapshot = PublicationStateSnapshot.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            _corrupt("publication snapshot", exc)
        if snapshot.plan_id != plan_id:
            _raise(PublicationWorkflowStoreErrorCode.CORRUPT, "snapshot file identity mismatch")
        return snapshot

    def compare_and_swap(
        self,
        plan_id: str,
        expected_state_version: int,
        snapshot: PublicationStateSnapshot,
        event: PublicationEvent,
    ) -> None:
        with self._lock:
            current = self.get_snapshot(plan_id)
            _validate_cas(plan_id, current, expected_state_version, snapshot, event)
            event_dir = self.events / _safe(plan_id)
            event_dir.mkdir(mode=0o700, exist_ok=True)
            event_path = event_dir / f"{event.state_version:020d}.json"
            try:
                _write_new(event_path, event.to_dict())
            except FileExistsError:
                persisted = self._read(event_path, "publication event")
                try:
                    recovered = PublicationEvent.from_dict(persisted)
                except (KeyError, TypeError, ValueError) as exc:
                    _corrupt("publication event", exc)
                if recovered != event:
                    _raise(
                        PublicationWorkflowStoreErrorCode.CORRUPT,
                        "publication event version contains a different transition",
                    )
            _write_atomic(self._path(self.snapshots, plan_id), snapshot.to_dict())

    def get_events(
        self, plan_id: str, *, after_state_version: int = 0
    ) -> tuple[PublicationEvent, ...]:
        self.get_plan(plan_id)
        event_dir = self.events / _safe(plan_id)
        result: list[PublicationEvent] = []
        for path in sorted(event_dir.glob("*.json")):
            value = self._read(path, "publication event")
            try:
                event = PublicationEvent.from_dict(value)
            except (KeyError, TypeError, ValueError) as exc:
                _corrupt("publication event", exc)
            if event.state_version > after_state_version:
                result.append(event)
        return tuple(result)

    def begin_operation(self, receipt: PublicationOperationReceipt) -> PublicationOperationReceipt:
        if receipt.status != PublicationOperationStatus.STARTED:
            raise ValueError("begin_operation requires a started receipt")
        with self._lock:
            path = self._path(self.receipts, receipt.idempotency_key)
            try:
                _write_new(path, receipt.to_dict())
                return receipt
            except FileExistsError:
                existing = self.get_operation(receipt.idempotency_key)
                assert existing is not None
                _same_receipt_or_conflict(existing, receipt)
                return existing

    def complete_operation(
        self, receipt: PublicationOperationReceipt
    ) -> PublicationOperationReceipt:
        if receipt.status == PublicationOperationStatus.STARTED:
            raise ValueError("complete_operation requires a completed receipt")
        with self._lock:
            existing = self.get_operation(receipt.idempotency_key)
            if existing is None:
                _raise(PublicationWorkflowStoreErrorCode.NOT_FOUND, "operation receipt not found")
            _same_receipt_or_conflict(existing, receipt)
            if existing.status != PublicationOperationStatus.STARTED:
                if existing == receipt:
                    return existing
                _raise(
                    PublicationWorkflowStoreErrorCode.RECEIPT_COMPLETED,
                    "operation receipt is already completed",
                )
            _write_atomic(self._path(self.receipts, receipt.idempotency_key), receipt.to_dict())
            return receipt

    def get_operation(self, idempotency_key: str) -> PublicationOperationReceipt | None:
        path = self._path(self.receipts, idempotency_key)
        if not path.exists():
            return None
        value = self._read(path, "publication operation receipt")
        try:
            receipt = PublicationOperationReceipt.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            _corrupt("publication operation receipt", exc)
        if receipt.idempotency_key != idempotency_key:
            _raise(PublicationWorkflowStoreErrorCode.CORRUPT, "receipt file identity mismatch")
        return receipt

    def _path(self, directory: Path, identifier: str) -> Path:
        return directory / f"{_safe(identifier)}.json"

    @staticmethod
    def _read(path: Path, label: str) -> Mapping[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _raise(PublicationWorkflowStoreErrorCode.NOT_FOUND, f"{label} not found")
        except (OSError, json.JSONDecodeError) as exc:
            _corrupt(label, exc)
        if not isinstance(value, Mapping):
            _raise(PublicationWorkflowStoreErrorCode.CORRUPT, f"{label} must be an object")
        return value


def _validate_initial(plan: PublicationPlan, snapshot: PublicationStateSnapshot) -> None:
    if snapshot.plan_id != plan.plan_id or snapshot.plan_digest != plan.plan_digest:
        raise ValueError("initial publication snapshot does not belong to plan")
    if snapshot.state_version != 1 or snapshot.state.value != "proposed":
        raise ValueError("initial publication snapshot must be proposed at version 1")


def _validate_cas(
    plan_id: str,
    current: PublicationStateSnapshot,
    expected: int,
    snapshot: PublicationStateSnapshot,
    event: PublicationEvent,
) -> None:
    if current.state_version != expected:
        _raise(PublicationWorkflowStoreErrorCode.STALE, "publication state version changed")
    if snapshot.plan_id != plan_id or snapshot.plan_digest != current.plan_digest:
        raise ValueError("replacement publication snapshot identity mismatch")
    if snapshot.state_version != expected + 1:
        raise ValueError("replacement publication snapshot must advance one version")
    if event.plan_digest != current.plan_digest or event.state_version != snapshot.state_version:
        raise ValueError("publication event does not match replacement snapshot")
    if event.from_state != current.state or event.to_state != snapshot.state:
        raise ValueError("publication event transition does not match snapshots")


def _same_receipt_or_conflict(
    existing: PublicationOperationReceipt, requested: PublicationOperationReceipt
) -> None:
    if not existing.same_operation(requested):
        _raise(
            PublicationWorkflowStoreErrorCode.IDEMPOTENCY_CONFLICT,
            "idempotency key was already used for different publication input",
        )


def _safe(value: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"unsafe publication store identifier: {value!r}")
    return value


def _digest(value: str) -> None:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError(f"invalid publication SHA-256 digest: {value!r}")


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    payload = canonical_json_bytes(value) + b"\n"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _write_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        _write_new(temporary, value)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("publication workflow store write made no progress")
        remaining = remaining[written:]


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _corrupt(label: str, exc: BaseException) -> Never:
    _raise(PublicationWorkflowStoreErrorCode.CORRUPT, f"invalid {label}: {exc}")


def _raise(code: PublicationWorkflowStoreErrorCode, message: str) -> Never:
    raise PublicationWorkflowStoreError(code, message)
