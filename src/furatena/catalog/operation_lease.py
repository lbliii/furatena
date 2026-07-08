"""Cross-process renewable filesystem leases for sync and deployment operations."""

from __future__ import annotations

import json
import math
import os
import shutil
import socket
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path
from types import TracebackType
from typing import Any

type _LeaseIdentity = tuple[int, int, str, float, float]


class OperationLeaseTimeout(TimeoutError):
    """Raised when an active operation lease outlives the acquisition timeout."""


class _OperationLeaseOwnershipLost(RuntimeError):
    """Internal signal that a lease directory changed during owner publication."""


class OperationLease:
    """Portable mkdir-based lease with heartbeat and stale-worker recovery."""

    def __init__(
        self,
        root: Path,
        name: str,
        *,
        resource: str = "",
        timeout_seconds: float = 30.0,
        lease_seconds: float = 3600.0,
        poll_seconds: float = 0.05,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.name = _name(name)
        self.resource = resource
        self.timeout_seconds = _positive(timeout_seconds, "timeout_seconds")
        self.lease_seconds = _positive(lease_seconds, "lease_seconds")
        self.poll_seconds = _positive(poll_seconds, "poll_seconds")
        self.path = self.root / f"{self.name}.lease"
        self.owner_path = self.path / "owner.json"
        self.token = uuid.uuid4().hex
        self._acquired = False
        self._stop = threading.Event()
        self._heartbeat: threading.Thread | None = None

    def acquire(self) -> OperationLease:
        deadline = time.monotonic() + self.timeout_seconds
        self.root.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self.path.mkdir(mode=0o700)
            except FileExistsError:
                expired_identity = self._expired_identity()
                if expired_identity is not None:
                    self._reclaim_expired(expired_identity)
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    owner = self.owner()
                    raise OperationLeaseTimeout(
                        f"operation lease {self.name!r} timed out after "
                        f"{self.timeout_seconds:g}s; owner={owner}"
                    ) from None
                self._stop.wait(min(self.poll_seconds, remaining))
                continue
            self._acquired = True
            try:
                self._claim_owner()
            except (FileExistsError, FileNotFoundError, _OperationLeaseOwnershipLost):
                # A stale-lease reclaimer may replace the directory after mkdir()
                # but before the first owner record is published. Treat that as a
                # lost acquisition and compete for the current directory instead
                # of overwriting a newer owner's record.
                self._acquired = False
                continue
            self._heartbeat = threading.Thread(
                target=self._heartbeat_loop,
                name=f"fura-lease-{self.name}",
                daemon=True,
            )
            self._heartbeat.start()
            return self

    def renew(self) -> None:
        if not self._acquired:
            raise RuntimeError("cannot renew an unacquired operation lease")
        owner = self.owner()
        if owner.get("token") != self.token:
            raise RuntimeError(f"operation lease {self.name!r} ownership was lost")
        self._write_owner(acquired_at=float(owner.get("acquired_at_epoch") or time.time()))

    def release(self) -> None:
        if not self._acquired:
            return
        self._stop.set()
        if self._heartbeat is not None and self._heartbeat is not threading.current_thread():
            self._heartbeat.join(timeout=min(self.lease_seconds, 1.0))
        owner = self.owner()
        if owner.get("token") == self.token:
            self.owner_path.unlink(missing_ok=True)
            with suppress(OSError):
                self.path.rmdir()
        self._acquired = False

    def owner(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.owner_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def __enter__(self) -> OperationLease:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    def _heartbeat_loop(self) -> None:
        interval = max(min(self.lease_seconds / 5.0, 30.0), 0.01)
        while not self._stop.wait(interval):
            try:
                self.renew()
            except (OSError, RuntimeError, ValueError):
                return

    def _owner_payload(self, *, acquired_at: float | None = None) -> dict[str, Any]:
        now = time.time()
        acquired = now if acquired_at is None else acquired_at
        return {
            "schema_version": 1,
            "name": self.name,
            "resource": self.resource,
            "token": self.token,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "acquired_at": _iso(acquired),
            "acquired_at_epoch": acquired,
            "renewed_at": _iso(now),
            "renewed_at_epoch": now,
            "expires_at": _iso(now + self.lease_seconds),
            "expires_at_epoch": now + self.lease_seconds,
        }

    def _claim_owner(self) -> None:
        """Publish the initial owner without replacing a newer lease owner."""
        payload = self._owner_payload()
        with self.owner_path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if self.owner().get("token") != self.token:
            raise _OperationLeaseOwnershipLost

    def _write_owner(self, *, acquired_at: float | None = None) -> None:
        payload = self._owner_payload(acquired_at=acquired_at)
        temporary = self.path / f".owner.{self.token}.tmp"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.owner_path)

    def _lease_identity(self) -> _LeaseIdentity | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        owner = self.owner()
        expires_at = owner.get("expires_at_epoch")
        expires = (
            float(expires_at)
            if expires_at is not None
            else stat.st_mtime + self.lease_seconds
        )
        return (
            stat.st_dev,
            stat.st_ino,
            str(owner.get("token") or ""),
            float(owner.get("renewed_at_epoch") or 0.0),
            expires,
        )

    def _expired_identity(self) -> _LeaseIdentity | None:
        identity = self._lease_identity()
        if identity is None or time.time() < identity[-1]:
            return None
        return identity

    def _reclaim_expired(self, expected_identity: _LeaseIdentity) -> None:
        current_identity = self._lease_identity()
        if current_identity is None:
            return
        if current_identity != expected_identity or time.time() < current_identity[-1]:
            return
        stale = self.root / f".{self.name}.stale.{uuid.uuid4().hex}"
        try:
            os.replace(self.path, stale)
        except OSError:
            return
        shutil.rmtree(stale, ignore_errors=True)


def operation_timeout_seconds() -> float:
    raw = os.environ.get("FURA_OPERATION_LOCK_TIMEOUT", "30").strip()
    return _positive(float(raw), "FURA_OPERATION_LOCK_TIMEOUT")


def operation_lease_seconds() -> float:
    raw = os.environ.get("FURA_OPERATION_LEASE_SECONDS", "3600").strip()
    return _positive(float(raw), "FURA_OPERATION_LEASE_SECONDS")


def _name(value: str) -> str:
    normalized = "".join(char if char.isalnum() or char in "-_" else "-" for char in value)
    if not normalized:
        raise ValueError("operation lease name is required")
    return normalized


def _positive(value: float, label: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"operation lease {label} must be finite and positive")
    return normalized


def _iso(value: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")
