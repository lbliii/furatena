"""Versioned, durable async refresh operations for one configured content source."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from furatena.catalog.content_deployment import (
    ContentDeploymentConflict,
    ContentDeploymentError,
    ContentDeploymentStore,
)
from furatena.catalog.operation_lease import (
    OperationLease,
    operation_lease_seconds,
    operation_timeout_seconds,
)

CONTENT_REFRESH_SCHEMA_VERSION = 1
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_OPERATION_PATTERN = re.compile(r"^refresh-[0-9a-f]{24}$")


class ContentRefreshState(StrEnum):
    QUEUED = "queued"
    STAGING = "staging"
    PROMOTED = "promoted"
    ACTIVATION_PENDING_RESTART = "activation_pending_restart"
    RESTART_SCHEDULED = "restart_scheduled"
    READY = "ready"
    FAILED = "failed"


_TERMINAL_STATES = frozenset({ContentRefreshState.READY, ContentRefreshState.FAILED})


class ContentRefreshConflict(ContentDeploymentError):
    """A deterministic HTTP/CLI conflict that has no hidden provider effect."""

    def __init__(self, code: str, message: str) -> None:
        self.code = _required(code, "conflict code")
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ContentRefreshActor:
    actor: str
    identity_source: str
    transport: str

    def __post_init__(self) -> None:
        for field_name in ("actor", "identity_source", "transport"):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))

    def to_dict(self) -> dict[str, str]:
        return {
            "actor": self.actor,
            "identity_source": self.identity_source,
            "transport": self.transport,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ContentRefreshActor:
        return cls(
            actor=str(value.get("actor") or ""),
            identity_source=str(value.get("identity_source") or ""),
            transport=str(value.get("transport") or ""),
        )


@dataclass(frozen=True, slots=True)
class ContentRefreshRequest:
    expected_active_commit: str | None
    requested_commit: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.expected_active_commit is not None:
            object.__setattr__(
                self,
                "expected_active_commit",
                _commit(self.expected_active_commit, "expected_active_commit"),
            )
        object.__setattr__(
            self, "requested_commit", _commit(self.requested_commit, "requested_commit")
        )
        key = _required(self.idempotency_key, "idempotency_key")
        if _KEY_PATTERN.fullmatch(key) is None:
            raise ValueError(
                "idempotency_key must be 8-128 safe ASCII characters beginning with a letter or digit."
            )
        object.__setattr__(self, "idempotency_key", key)

    def to_dict(self, *, include_key: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": CONTENT_REFRESH_SCHEMA_VERSION,
            "record_type": "furatena.content-refresh.request",
            "expected_active_commit": self.expected_active_commit,
            "requested_commit": self.requested_commit,
        }
        if include_key:
            payload["idempotency_key"] = self.idempotency_key
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ContentRefreshRequest:
        if value.get("schema_version") != CONTENT_REFRESH_SCHEMA_VERSION:
            raise ValueError("The content refresh request uses an unsupported schema version.")
        if value.get("record_type") != "furatena.content-refresh.request":
            raise ValueError("The content refresh request uses an unsupported record type.")
        allowed = {
            "schema_version",
            "record_type",
            "expected_active_commit",
            "requested_commit",
            "idempotency_key",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(
                f"The content refresh request contains unsupported fields: {', '.join(unknown)}."
            )
        expected = value.get("expected_active_commit")
        return cls(
            expected_active_commit=str(expected) if expected is not None else None,
            requested_commit=str(value.get("requested_commit") or ""),
            idempotency_key=str(value.get("idempotency_key") or ""),
        )


@dataclass(frozen=True, slots=True)
class ContentRefreshEvent:
    state: ContentRefreshState
    recorded_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", ContentRefreshState(self.state))
        object.__setattr__(self, "recorded_at", _required(self.recorded_at, "recorded_at"))

    def to_dict(self) -> dict[str, str]:
        return {"state": self.state.value, "recorded_at": self.recorded_at}


@dataclass(frozen=True, slots=True)
class ContentRefreshReceipt:
    operation_id: str
    semantic_digest: str
    idempotency_key_digest: str
    actor: ContentRefreshActor
    expected_active_commit: str | None
    requested_commit: str | None
    configured_repository_digest: str
    configured_ref: str
    configured_subdirectory: str
    state: ContentRefreshState
    active_commit_before: str | None
    resolved_commit: str | None
    generation: str | None
    restart_required: bool
    restart_scheduled: bool
    readiness: Mapping[str, Any] | None
    failure: Mapping[str, str] | None
    compatibility_mode: bool
    replayed: bool
    created_at: str
    updated_at: str
    history: tuple[ContentRefreshEvent, ...]

    def __post_init__(self) -> None:
        if _OPERATION_PATTERN.fullmatch(self.operation_id) is None:
            raise ValueError(
                "The content refresh receipt contains an invalid operation identifier."
            )
        for field_name in (
            "semantic_digest",
            "idempotency_key_digest",
            "configured_repository_digest",
        ):
            value = str(getattr(self, field_name))
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                raise ValueError(
                    f"The content refresh receipt contains an invalid {field_name} digest."
                )
        object.__setattr__(self, "state", ContentRefreshState(self.state))
        for field_name in ("configured_ref", "configured_subdirectory", "created_at", "updated_at"):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))
        for field_name in (
            "expected_active_commit",
            "requested_commit",
            "active_commit_before",
            "resolved_commit",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _commit(value, field_name))
        history = tuple(self.history)
        if not history or history[-1].state != self.state:
            raise ValueError("The content refresh history must end at the current state.")
        object.__setattr__(self, "history", history)
        if self.state == ContentRefreshState.FAILED and self.failure is None:
            raise ValueError("A failed content refresh receipt must include failure details.")
        if self.state != ContentRefreshState.FAILED and self.failure is not None:
            raise ValueError("Content refresh failure details require the receipt failed state.")

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CONTENT_REFRESH_SCHEMA_VERSION,
            "record_type": "furatena.content-refresh.receipt",
            "operation_id": self.operation_id,
            "semantic_digest": self.semantic_digest,
            "idempotency_key_digest": self.idempotency_key_digest,
            "actor": self.actor.to_dict(),
            "expected_active_commit": self.expected_active_commit,
            "requested_commit": self.requested_commit,
            "configured_repository_digest": self.configured_repository_digest,
            "configured_ref": self.configured_ref,
            "configured_subdirectory": self.configured_subdirectory,
            "state": self.state.value,
            "active_commit_before": self.active_commit_before,
            "resolved_commit": self.resolved_commit,
            "generation": self.generation,
            "restart_required": self.restart_required,
            "restart_scheduled": self.restart_scheduled,
            "readiness": dict(self.readiness) if self.readiness is not None else None,
            "failure": dict(self.failure) if self.failure is not None else None,
            "compatibility_mode": self.compatibility_mode,
            "replayed": self.replayed,
            "terminal": self.terminal,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "history": [event.to_dict() for event in self.history],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ContentRefreshReceipt:
        if value.get("schema_version") != CONTENT_REFRESH_SCHEMA_VERSION:
            raise ValueError("The content refresh receipt uses an unsupported schema version.")
        if value.get("record_type") != "furatena.content-refresh.receipt":
            raise ValueError("The content refresh receipt uses an unsupported record type.")
        return cls(
            operation_id=str(value.get("operation_id") or ""),
            semantic_digest=str(value.get("semantic_digest") or ""),
            idempotency_key_digest=str(value.get("idempotency_key_digest") or ""),
            actor=ContentRefreshActor.from_dict(_mapping(value.get("actor"), "actor")),
            expected_active_commit=_optional_commit(value.get("expected_active_commit")),
            requested_commit=_optional_commit(value.get("requested_commit")),
            configured_repository_digest=str(value.get("configured_repository_digest") or ""),
            configured_ref=str(value.get("configured_ref") or ""),
            configured_subdirectory=str(value.get("configured_subdirectory") or ""),
            state=ContentRefreshState(str(value.get("state") or "")),
            active_commit_before=_optional_commit(value.get("active_commit_before")),
            resolved_commit=_optional_commit(value.get("resolved_commit")),
            generation=str(value["generation"]) if value.get("generation") else None,
            restart_required=bool(value.get("restart_required", False)),
            restart_scheduled=bool(value.get("restart_scheduled", False)),
            readiness=(
                _mapping(value.get("readiness"), "readiness")
                if value.get("readiness") is not None
                else None
            ),
            failure=(
                {
                    str(key): str(item)
                    for key, item in _mapping(value.get("failure"), "failure").items()
                }
                if value.get("failure") is not None
                else None
            ),
            compatibility_mode=bool(value.get("compatibility_mode", False)),
            replayed=bool(value.get("replayed", False)),
            created_at=str(value.get("created_at") or ""),
            updated_at=str(value.get("updated_at") or ""),
            history=tuple(
                ContentRefreshEvent(
                    state=ContentRefreshState(str(item.get("state") or "")),
                    recorded_at=str(item.get("recorded_at") or ""),
                )
                for raw in _sequence(value.get("history"))
                if (item := _mapping(raw, "history item"))
            ),
        )

    def public_dict(
        self,
        *,
        status_url: str,
        include_failure_message: bool = True,
    ) -> dict[str, object]:
        failure: dict[str, str] | None = None
        if self.failure is not None:
            failure = {"code": str(self.failure.get("code") or "refresh_failed")}
            if include_failure_message:
                failure["message"] = str(self.failure.get("message") or "Content refresh failed.")
        return {
            "schema_version": CONTENT_REFRESH_SCHEMA_VERSION,
            "operation_id": self.operation_id,
            "state": self.state.value,
            "status_url": status_url,
            "resolved_commit": self.resolved_commit,
            "generation": self.generation,
            "restart_required": self.restart_required,
            "restart_scheduled": self.restart_scheduled,
            "readiness": dict(self.readiness) if self.readiness is not None else None,
            "failure": failure,
            "compatibility_mode": self.compatibility_mode,
            "replayed": self.replayed,
            "terminal": self.terminal,
        }


type RestartScheduler = Callable[[], bool]
type Clock = Callable[[], float]


class ContentRefreshService:
    """One operation model shared by HTTP, CLI, and startup reconciliation."""

    def __init__(
        self,
        store: ContentDeploymentStore,
        *,
        restart_scheduler: RestartScheduler = lambda: False,
        clock: Clock = time.time,
    ) -> None:
        self.store = store
        self.operations = store.root / "operations"
        self.restart_scheduler = restart_scheduler
        self.clock = clock
        self._threads: set[threading.Thread] = set()
        self._thread_lock = threading.RLock()

    def submit(
        self,
        request: ContentRefreshRequest,
        *,
        actor: ContentRefreshActor,
        asynchronous: bool = True,
    ) -> ContentRefreshReceipt:
        digest = self._semantic_digest(request, actor)
        operation_id = _operation_id(request.idempotency_key)
        refresh_lease: OperationLease | None = None
        try:
            with self._submission_lease():
                existing = self._read(operation_id)
                if existing is not None:
                    if existing.semantic_digest != digest:
                        raise ContentRefreshConflict(
                            code="idempotency_key_collision",
                            message="The idempotency key is already bound to a different refresh request.",
                        )
                    return replace(existing, replayed=True)
                current_commit = self._active_commit()
                if current_commit != request.expected_active_commit:
                    raise ContentRefreshConflict(
                        code="stale_active_commit",
                        message="The expected active content commit no longer matches the selected generation.",
                    )
                if self._pending_receipts():
                    raise ContentRefreshConflict(
                        code="refresh_in_progress",
                        message="Another content refresh is already pending for this single-replica service.",
                    )
                try:
                    resolved = self.store.resolve_requested_commit(request.requested_commit)
                except ContentDeploymentConflict as exc:
                    raise ContentRefreshConflict(code=exc.code, message=str(exc)) from exc
                except ContentDeploymentError as exc:
                    raise ContentRefreshConflict(
                        code="unreachable_commit",
                        message="The exact requested commit could not be proven reachable under the configured ref policy.",
                    ) from exc
                if resolved != request.requested_commit:
                    raise ContentRefreshConflict(
                        code="unreachable_commit",
                        message="The exact requested commit is not reachable under the configured ref policy.",
                    )
                receipt = self._initial_receipt(
                    operation_id,
                    digest,
                    request,
                    actor,
                    active_commit=current_commit,
                    compatibility_mode=False,
                )
                refresh_lease = self.store._content_refresh_lease()
                refresh_lease.acquire()
                self._write(receipt)
        except BaseException as exc:
            if refresh_lease is not None:
                self._release_transferred_lease(refresh_lease, exc)
            raise
        assert refresh_lease is not None
        if asynchronous:
            try:
                self._start(lambda: self._run_with_lease(receipt, request, refresh_lease))
            except BaseException as exc:
                self._release_transferred_lease(refresh_lease, exc)
                raise
            return receipt
        return self._run_with_lease(receipt, request, refresh_lease)

    def submit_compatibility(
        self,
        *,
        actor: ContentRefreshActor,
        asynchronous: bool = True,
    ) -> ContentRefreshReceipt:
        key = f"legacy-{os.urandom(16).hex()}"
        operation_id = _operation_id(key)
        semantic = _digest(
            {
                "compatibility_mode": True,
                "nonce": key,
                "scope": self._scope(),
                "actor": actor.to_dict(),
            }
        )
        refresh_lease: OperationLease | None = None
        try:
            with self._submission_lease():
                if self._pending_receipts():
                    raise ContentRefreshConflict(
                        code="refresh_in_progress",
                        message="Another content refresh is already pending for this single-replica service.",
                    )
                receipt = self._initial_receipt(
                    operation_id,
                    semantic,
                    None,
                    actor,
                    active_commit=self._active_commit(),
                    compatibility_mode=True,
                    idempotency_key=key,
                )
                refresh_lease = self.store._content_refresh_lease()
                refresh_lease.acquire()
                self._write(receipt)
        except BaseException as exc:
            if refresh_lease is not None:
                self._release_transferred_lease(refresh_lease, exc)
            raise
        assert refresh_lease is not None
        if asynchronous:
            try:
                self._start(lambda: self._run_compatibility_with_lease(receipt, refresh_lease))
            except BaseException as exc:
                self._release_transferred_lease(refresh_lease, exc)
                raise
            return receipt
        return self._run_compatibility_with_lease(receipt, refresh_lease)

    def get(self, operation_id: str) -> ContentRefreshReceipt | None:
        if _OPERATION_PATTERN.fullmatch(operation_id) is None:
            return None
        return self._read(operation_id)

    def latest(self) -> ContentRefreshReceipt | None:
        receipts = self._receipts()
        return max(receipts, key=lambda item: (item.updated_at, item.operation_id), default=None)

    def reconcile_startup(self) -> tuple[ContentRefreshReceipt, ...]:
        """Locally reconcile promoted operations against the generation this process will serve."""
        with self._submission_lease(), self.store._content_refresh_lease():
            active = self.store._status_owned()
            return self._reconcile_startup_snapshot(active)

    def _reconcile_startup_snapshot(
        self, active: Mapping[str, Any]
    ) -> tuple[ContentRefreshReceipt, ...]:
        """Reconcile receipts against a selection snapshot owned by both operation leases."""
        active_generation = active.get("active_generation")
        raw_active_receipt = active.get("receipt")
        active_receipt: dict[str, Any] = (
            raw_active_receipt if isinstance(raw_active_receipt, dict) else {}
        )
        active_commit = str(active_receipt.get("resolved_ref") or "") or None
        image_digest = os.environ.get("FURA_IMAGE_DIGEST", "unknown").strip() or "unknown"
        build_commit = (
            os.environ.get("FURA_BUILD_GIT_SHA")
            or os.environ.get("RAILWAY_GIT_COMMIT_SHA")
            or "unknown"
        ).strip() or "unknown"
        identity_matches = (
            active_receipt.get("image_digest") == image_digest
            and active_receipt.get("build_commit", "unknown") == build_commit
        )
        updated: list[ContentRefreshReceipt] = []
        for receipt in self._receipts():
            if receipt.state in {ContentRefreshState.QUEUED, ContentRefreshState.STAGING}:
                updated.append(
                    self._failed(
                        receipt,
                        "interrupted_before_promotion",
                        "The prior process ended before content promotion completed; active content was preserved.",
                    )
                )
                continue
            if receipt.state not in {
                ContentRefreshState.PROMOTED,
                ContentRefreshState.ACTIVATION_PENDING_RESTART,
                ContentRefreshState.RESTART_SCHEDULED,
            }:
                continue
            selection_matches = (
                receipt.generation == active_generation and receipt.resolved_commit == active_commit
            )
            if selection_matches and identity_matches:
                readiness = {
                    "ready": True,
                    "active_generation": active_generation,
                    "resolved_commit": active_commit,
                    "image_digest": image_digest,
                    "build_commit": build_commit,
                }
                receipt = self._transition(
                    receipt,
                    ContentRefreshState.READY,
                    readiness=readiness,
                )
                updated.append(receipt)
            else:
                code = (
                    "generation_runtime_incompatible"
                    if selection_matches
                    else "superseded_generation"
                )
                message = (
                    "The selected generation does not match the running image or build identity."
                    if selection_matches
                    else "The promoted generation is no longer selected; a later rollback or promotion superseded it."
                )
                updated.append(
                    self._failed(
                        receipt,
                        code,
                        message,
                    )
                )
        return tuple(updated)

    def rollback(self, *, actor: str, reason: str) -> dict[str, Any]:
        """Roll back content and terminalize refreshes superseded by that selection."""

        def supersede_selected(result: Mapping[str, Any]) -> None:
            superseded_generation = str(result.get("last_known_good_generation") or "")
            for receipt in self._receipts():
                if (
                    receipt.state
                    not in {
                        ContentRefreshState.PROMOTED,
                        ContentRefreshState.ACTIVATION_PENDING_RESTART,
                        ContentRefreshState.RESTART_SCHEDULED,
                        ContentRefreshState.READY,
                    }
                    or receipt.generation != superseded_generation
                ):
                    continue
                self._failed(
                    receipt,
                    "superseded_by_rollback",
                    "The content refresh was superseded by an operator rollback.",
                )

        with self._submission_lease():
            return self.store.rollback(actor=actor, reason=reason, completion=supersede_selected)

    def _run_with_lease(
        self,
        receipt: ContentRefreshReceipt,
        request: ContentRefreshRequest,
        lease: OperationLease,
    ) -> ContentRefreshReceipt:
        try:
            return self._run(receipt, request)
        finally:
            lease.release()

    @staticmethod
    def _release_transferred_lease(lease: OperationLease, original: BaseException) -> None:
        """Release provisional ownership while preserving the triggering exception."""
        try:
            lease.release()
        except BaseException as cleanup_error:
            original.add_note(
                "The provisional content-refresh lease also failed to release cleanly "
                f"({cleanup_error.__class__.__name__})."
            )

    def _run_compatibility_with_lease(
        self,
        receipt: ContentRefreshReceipt,
        lease: OperationLease,
    ) -> ContentRefreshReceipt:
        try:
            return self._run_compatibility(receipt)
        finally:
            lease.release()

    def _run(
        self, receipt: ContentRefreshReceipt, request: ContentRefreshRequest
    ) -> ContentRefreshReceipt:
        try:
            receipt = self._transition(receipt, ContentRefreshState.STAGING)
            completed: ContentRefreshReceipt | None = None

            def complete(result: Mapping[str, Any]) -> None:
                nonlocal completed
                completed = self._after_promotion(receipt, result)

            self.store.refresh(
                trigger=receipt.actor.transport,
                expected_active_commit=request.expected_active_commit,
                requested_commit=request.requested_commit,
                actor=receipt.actor.actor,
                operation_id=receipt.operation_id,
                semantic_digest=receipt.semantic_digest,
                idempotency_key_digest=receipt.idempotency_key_digest,
                completion=complete,
                _lease_owned=True,
            )
            if completed is None:  # pragma: no cover - store contract guard
                raise RuntimeError("The content refresh completed without durable bookkeeping.")
            return completed
        except ContentDeploymentConflict as exc:
            return self._failed(receipt, exc.code, str(exc))
        except ContentDeploymentError as exc:
            return self._failed(receipt, getattr(exc, "code", "refresh_failed"), str(exc))
        except Exception as exc:
            return self._failed(
                receipt,
                "refresh_failed",
                str(exc) or exc.__class__.__name__,
            )

    def _run_compatibility(self, receipt: ContentRefreshReceipt) -> ContentRefreshReceipt:
        try:
            receipt = self._transition(receipt, ContentRefreshState.STAGING)
            completed: ContentRefreshReceipt | None = None

            def complete(result: Mapping[str, Any]) -> None:
                nonlocal completed
                completed = self._after_promotion(receipt, result)

            self.store.refresh(
                trigger="http-empty-body-compatibility",
                actor=receipt.actor.actor,
                operation_id=receipt.operation_id,
                semantic_digest=receipt.semantic_digest,
                idempotency_key_digest=receipt.idempotency_key_digest,
                completion=complete,
                _lease_owned=True,
            )
            if completed is None:  # pragma: no cover - store contract guard
                raise RuntimeError("The content refresh completed without durable bookkeeping.")
            return completed
        except Exception as exc:
            code = getattr(exc, "code", "refresh_failed")
            return self._failed(receipt, code, str(exc) or exc.__class__.__name__)

    def _after_promotion(
        self, receipt: ContentRefreshReceipt, result: Mapping[str, Any]
    ) -> ContentRefreshReceipt:
        resolved = str(result.get("resolved_ref") or "") or None
        generation = str(result.get("generation") or "") or None
        if result.get("operation") == "no_change":
            readiness = {
                "ready": True,
                "active_generation": generation,
                "resolved_commit": resolved,
                "no_change": True,
            }
            return self._transition(
                receipt,
                ContentRefreshState.READY,
                resolved_commit=resolved,
                generation=generation,
                readiness=readiness,
            )
        receipt = self._transition(
            receipt,
            ContentRefreshState.PROMOTED,
            resolved_commit=resolved,
            generation=generation,
            restart_required=True,
        )
        receipt = self._transition(
            receipt,
            ContentRefreshState.ACTIVATION_PENDING_RESTART,
            restart_required=True,
        )
        try:
            scheduled = bool(self.restart_scheduler())
        except Exception:
            return receipt
        if scheduled:
            receipt = self._transition(
                receipt,
                ContentRefreshState.RESTART_SCHEDULED,
                restart_required=True,
                restart_scheduled=True,
            )
        return receipt

    def _failed(
        self, receipt: ContentRefreshReceipt, code: str, message: str
    ) -> ContentRefreshReceipt:
        return self._transition(
            receipt,
            ContentRefreshState.FAILED,
            failure={"code": _required(code, "failure code"), "message": _safe_message(message)},
        )

    def _transition(
        self,
        receipt: ContentRefreshReceipt,
        state: ContentRefreshState,
        **changes: Any,
    ) -> ContentRefreshReceipt:
        recorded_at = _iso(self.clock())
        updated = replace(
            receipt,
            state=state,
            updated_at=recorded_at,
            history=(*receipt.history, ContentRefreshEvent(state, recorded_at)),
            replayed=False,
            **changes,
        )
        self._write(updated)
        return updated

    def _initial_receipt(
        self,
        operation_id: str,
        semantic_digest: str,
        request: ContentRefreshRequest | None,
        actor: ContentRefreshActor,
        *,
        active_commit: str | None,
        compatibility_mode: bool,
        idempotency_key: str | None = None,
    ) -> ContentRefreshReceipt:
        now = _iso(self.clock())
        key = request.idempotency_key if request is not None else _required(idempotency_key, "key")
        return ContentRefreshReceipt(
            operation_id=operation_id,
            semantic_digest=semantic_digest,
            idempotency_key_digest=_text_digest(key),
            actor=actor,
            expected_active_commit=(request.expected_active_commit if request else None),
            requested_commit=(request.requested_commit if request else None),
            configured_repository_digest=_text_digest(self.store.config.repository),
            configured_ref=self.store.config.ref,
            configured_subdirectory=self.store.config.subdirectory,
            state=ContentRefreshState.QUEUED,
            active_commit_before=active_commit,
            resolved_commit=None,
            generation=None,
            restart_required=False,
            restart_scheduled=False,
            readiness=None,
            failure=None,
            compatibility_mode=compatibility_mode,
            replayed=False,
            created_at=now,
            updated_at=now,
            history=(ContentRefreshEvent(ContentRefreshState.QUEUED, now),),
        )

    def _semantic_digest(self, request: ContentRefreshRequest, actor: ContentRefreshActor) -> str:
        return _digest(
            {
                "schema_version": CONTENT_REFRESH_SCHEMA_VERSION,
                "scope": self._scope(),
                "expected_active_commit": request.expected_active_commit,
                "requested_commit": request.requested_commit,
                "actor": actor.to_dict(),
            }
        )

    def _scope(self) -> dict[str, str]:
        return {
            "repository_digest": _text_digest(self.store.config.repository),
            "ref": self.store.config.ref,
            "subdirectory": self.store.config.subdirectory,
        }

    def _active_commit(self) -> str | None:
        status = self.store.status()
        receipt = status.get("receipt")
        if not isinstance(receipt, dict):
            return None
        return str(receipt.get("resolved_ref") or "") or None

    def _pending_receipts(self) -> tuple[ContentRefreshReceipt, ...]:
        return tuple(receipt for receipt in self._receipts() if not receipt.terminal)

    def _receipts(self) -> tuple[ContentRefreshReceipt, ...]:
        if not self.operations.is_dir():
            return ()
        values = []
        for path in sorted(self.operations.glob("refresh-*.json")):
            value = _read_json(path)
            if value is not None:
                values.append(ContentRefreshReceipt.from_dict(value))
        return tuple(values)

    def _read(self, operation_id: str) -> ContentRefreshReceipt | None:
        value = _read_json(self.operations / f"{operation_id}.json")
        return ContentRefreshReceipt.from_dict(value) if value is not None else None

    def _write(self, receipt: ContentRefreshReceipt) -> None:
        _write_json(self.operations / f"{receipt.operation_id}.json", receipt.to_dict())

    def _submission_lease(self) -> OperationLease:
        return OperationLease(
            self.store.leases,
            "content-refresh-submit",
            resource=self.store.config.repository,
            timeout_seconds=operation_timeout_seconds(),
            lease_seconds=operation_lease_seconds(),
        )

    def _start(self, target: Callable[[], object]) -> None:
        def run() -> None:
            try:
                target()
            finally:
                with self._thread_lock:
                    self._threads.discard(threading.current_thread())

        thread = threading.Thread(target=run, name="furatena-content-refresh", daemon=True)
        with self._thread_lock:
            self._threads.add(thread)
        thread.start()


def authenticate_content_actor(
    authorization: str | None,
    *,
    transport: str,
    environ: Mapping[str, str] | None = None,
) -> ContentRefreshActor | None:
    """Derive one actor from the configured bearer credential and trusted transport."""
    values = os.environ if environ is None else environ
    token = values.get("FURA_CONTENT_REFRESH_TOKEN", "").strip()
    scheme, separator, provided = (authorization or "").partition(" ")
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not token
        or len(token) < 32
        or not hmac.compare_digest(token, provided.strip())
    ):
        return None
    return ContentRefreshActor(
        actor="configured-content-operator",
        identity_source="content-refresh-bearer",
        transport=transport,
    )


def cli_content_actor() -> ContentRefreshActor:
    return ContentRefreshActor(
        actor=f"local-uid-{os.getuid()}",
        identity_source="local-process-identity",
        transport="cli",
    )


def _operation_id(key: str) -> str:
    return f"refresh-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}"


def _text_digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _commit(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if _SHA_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{label} must be an exact lowercase 40-character Git commit")
    return normalized


def _optional_commit(value: object) -> str | None:
    return _commit(str(value), "commit") if value is not None else None


def _required(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"content refresh {label} is required")
    return normalized


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"content refresh {label} must be an object")
    return {str(key): item for key, item in value.items()}


def _sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError("content refresh history must be an array")
    return tuple(value)


def _safe_message(value: str) -> str:
    message = str(value or "Content refresh failed.").strip()
    return message[:500]


def _iso(timestamp: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    import uuid

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


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
