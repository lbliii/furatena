"""Durable public-Git content generations for proprietary image deployments."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from furatena.catalog.operation_lease import (
    OperationLease,
    operation_lease_seconds,
    operation_timeout_seconds,
)

_TRUE = frozenset({"1", "true", "yes", "on"})
_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,254}$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class ContentDeploymentError(RuntimeError):
    """A content generation could not be safely prepared or selected."""


@dataclass(frozen=True, slots=True)
class ContentDeploymentConfig:
    """Validated non-secret content-source and state configuration."""

    repository: str
    ref: str
    subdirectory: str
    allowed_hosts: frozenset[str]
    state_root: Path
    max_bytes: int = 100 * 1024 * 1024
    max_files: int = 20_000
    refresh_on_start: bool = True

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> ContentDeploymentConfig | None:
        values = os.environ if environ is None else environ
        repository = values.get("FURA_CONTENT_REPOSITORY", "").strip()
        if not repository:
            return None
        allowed_hosts = frozenset(
            host.strip().lower()
            for host in values.get("FURA_CONTENT_ALLOWED_HOSTS", "github.com").split(",")
            if host.strip()
        )
        if not allowed_hosts:
            raise ContentDeploymentError(
                "FURA_CONTENT_ALLOWED_HOSTS must include at least one permitted public Git hostname."
            )
        parsed = urlsplit(repository)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.hostname.lower() not in allowed_hosts
        ):
            raise ContentDeploymentError(
                "FURA_CONTENT_REPOSITORY must be an HTTPS public Git URL without "
                "credentials, query, or fragment on an allowed host."
            )
        ref = values.get("FURA_CONTENT_REF", "main").strip()
        if _REF_PATTERN.fullmatch(ref) is None or ref.startswith("-") or ".." in ref:
            raise ContentDeploymentError(
                "FURA_CONTENT_REF contains unsafe Git reference syntax and cannot be resolved."
            )
        subdirectory = values.get("FURA_CONTENT_SUBDIRECTORY", "app").strip().strip("/")
        subpath = Path(subdirectory)
        if not subdirectory or subpath.is_absolute() or ".." in subpath.parts:
            raise ContentDeploymentError(
                "FURA_CONTENT_SUBDIRECTORY must name a safe relative application path."
            )
        state_root = (
            Path(values.get("FURA_CONTENT_STATE_ROOT", "/data/furatena")).expanduser().resolve()
        )
        max_bytes = _positive_int(values.get("FURA_CONTENT_MAX_BYTES", "104857600"), "bytes")
        max_files = _positive_int(values.get("FURA_CONTENT_MAX_FILES", "20000"), "files")
        return cls(
            repository=repository,
            ref=ref,
            subdirectory=subpath.as_posix(),
            allowed_hosts=allowed_hosts,
            state_root=state_root,
            max_bytes=max_bytes,
            max_files=max_files,
            refresh_on_start=values.get("FURA_CONTENT_REFRESH_ON_START", "1").strip().lower()
            in _TRUE,
        )


class ContentDeploymentStore:
    """Stage, validate, freeze, promote, reconcile, and roll back generations."""

    def __init__(
        self,
        config: ContentDeploymentConfig,
        *,
        freezer: Callable[[Path, Path], Mapping[str, Any]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.root = config.state_root
        self.generations = self.root / "generations"
        self.staging = self.root / "staging"
        self.receipts = self.root / "receipts"
        self.state = self.root / "state"
        self.active = self.root / "active"
        self.last_known_good = self.root / "last-known-good"
        self.leases = self.root / "leases"
        self._freezer = freezer or self._freeze
        self._clock = clock

    def refresh(self, *, trigger: str = "manual") -> dict[str, Any]:
        """Build and atomically activate one immutable generation."""
        with OperationLease(
            self.leases,
            "content-refresh",
            resource=self.config.repository,
            timeout_seconds=operation_timeout_seconds(),
            lease_seconds=operation_lease_seconds(),
        ):
            self.reconcile()
            started = self._clock()
            attempt_id = uuid.uuid4().hex
            workspace = self.staging / attempt_id
            source = workspace / "source"
            frozen = workspace / "frozen"
            self.staging.mkdir(parents=True, exist_ok=True)
            try:
                resolved_ref = self._checkout(source)
                source_bytes, source_files = self._validate_source(source)
                current = self._active_receipt()
                image_digest = os.environ.get("FURA_IMAGE_DIGEST", "unknown").strip() or "unknown"
                if (
                    current is not None
                    and current.get("resolved_ref") == resolved_ref
                    and current.get("image_digest") == image_digest
                ):
                    if trigger != "startup":
                        self._set_rollback_hold(False)
                    return {
                        **current,
                        "operation": "no_change",
                        "trigger": trigger,
                        "checked_at": _iso(self._clock()),
                    }
                app_root = (source / self.config.subdirectory).resolve()
                if (
                    not app_root.is_relative_to(source.resolve())
                    or not (app_root / "docs.yaml").is_file()
                ):
                    raise ContentDeploymentError(
                        "FURA_CONTENT_SUBDIRECTORY="
                        f"{self.config.subdirectory} must contain a docs.yaml configuration file; "
                        "correct the configured subdirectory and retry."
                    )
                freeze = dict(self._freezer(app_root, frozen))
                self._validate_frozen(frozen, freeze)
                generation_id = self._generation_id(resolved_ref, started)
                generation = self.generations / generation_id
                if generation.exists():
                    raise ContentDeploymentError(
                        "Managed content generation already exists and cannot be replaced: "
                        f"{generation_id}."
                    )
                receipt = {
                    "schema_version": 1,
                    "generation": generation_id,
                    "status": "active",
                    "trigger": trigger,
                    "repository": self.config.repository,
                    "requested_ref": self.config.ref,
                    "resolved_ref": resolved_ref,
                    "image_digest": image_digest,
                    "subdirectory": self.config.subdirectory,
                    "source_bytes": source_bytes,
                    "source_files": source_files,
                    "page_count": int(freeze.get("page_count") or 0),
                    "frozen_root": str(freeze.get("frozen_root") or frozen),
                    "started_at": _iso(started),
                    "promoted_at": _iso(self._clock()),
                }
                workspace_receipt = workspace / "receipt.json"
                _write_json(workspace_receipt, receipt)
                self.generations.mkdir(parents=True, exist_ok=True)
                os.replace(workspace, generation)
                receipt["frozen_root"] = str(generation / "frozen")
                _write_json(generation / "receipt.json", receipt)
                previous = self._link_target(self.active)
                if previous is not None:
                    self._replace_link(self.last_known_good, previous)
                self._replace_link(self.active, generation)
                self.receipts.mkdir(parents=True, exist_ok=True)
                _write_json(self.receipts / f"{generation_id}.json", receipt)
                self.state.mkdir(parents=True, exist_ok=True)
                _write_json(
                    self.state / "current.json",
                    {
                        "schema_version": 1,
                        "active_generation": generation_id,
                        "last_known_good_generation": previous.name if previous else None,
                        "rollback_hold": False,
                        "updated_at": receipt["promoted_at"],
                    },
                )
                self._prune_generations(keep=5)
                return receipt
            except BaseException as exc:
                failure = {
                    "schema_version": 1,
                    "attempt": attempt_id,
                    "status": "failed",
                    "trigger": trigger,
                    "repository": self.config.repository,
                    "requested_ref": self.config.ref,
                    "failed_at": _iso(self._clock()),
                    "error": str(exc) or exc.__class__.__name__,
                }
                self.receipts.mkdir(parents=True, exist_ok=True)
                _write_json(self.receipts / f"failed-{attempt_id}.json", failure)
                raise
            finally:
                shutil.rmtree(workspace, ignore_errors=True)

    def reconcile(self) -> dict[str, Any]:
        """Repair safe crash residue and report the selected generation."""
        for workspace in self.staging.glob("*") if self.staging.is_dir() else ():
            if workspace.is_dir():
                shutil.rmtree(workspace, ignore_errors=True)
        active = self._link_target(self.active)
        lkg = self._link_target(self.last_known_good)
        repaired = False
        if active is None and lkg is not None:
            self._replace_link(self.active, lkg)
            active = lkg
            repaired = True
        if active is None:
            state = self._read_state()
            candidate = self.generations / str(state.get("active_generation") or "")
            if candidate.is_dir() and self._generation_valid(candidate):
                self._replace_link(self.active, candidate)
                active = candidate
                repaired = True
        return {
            "configured": True,
            "status": "ready" if active is not None else "uninitialized",
            "active_generation": active.name if active is not None else None,
            "last_known_good_generation": lkg.name if lkg is not None else None,
            "repaired": repaired,
            "rollback_hold": bool(self._read_state().get("rollback_hold")),
        }

    def rollback(self) -> dict[str, Any]:
        """Atomically select last-known-good and retain the prior active generation."""
        with OperationLease(
            self.leases,
            "content-refresh",
            resource=self.config.repository,
            timeout_seconds=operation_timeout_seconds(),
            lease_seconds=operation_lease_seconds(),
        ):
            active = self._link_target(self.active)
            lkg = self._link_target(self.last_known_good)
            if lkg is None:
                raise ContentDeploymentError(
                    "No valid last-known-good content generation is available for rollback."
                )
            self._replace_link(self.active, lkg)
            if active is not None:
                self._replace_link(self.last_known_good, active)
            receipt = {
                "schema_version": 1,
                "operation": "rollback",
                "status": "active",
                "active_generation": lkg.name,
                "last_known_good_generation": active.name if active else None,
                "recorded_at": _iso(self._clock()),
            }
            self.receipts.mkdir(parents=True, exist_ok=True)
            _write_json(self.receipts / f"rollback-{uuid.uuid4().hex}.json", receipt)
            _write_json(
                self.state / "current.json",
                {
                    "schema_version": 1,
                    "active_generation": lkg.name,
                    "last_known_good_generation": active.name if active else None,
                    "rollback_hold": True,
                    "updated_at": receipt["recorded_at"],
                },
            )
            return receipt

    def status(self) -> dict[str, Any]:
        status = self.reconcile()
        active = self._link_target(self.active)
        status["repository"] = self.config.repository
        status["requested_ref"] = self.config.ref
        status["subdirectory"] = self.config.subdirectory
        status["receipt"] = _read_json(active / "receipt.json") if active is not None else None
        return status

    def startup_refresh_needed(self) -> bool:
        """Return whether startup may follow the desired ref without undoing rollback."""
        status = self.reconcile()
        if status["active_generation"] is None:
            return True
        if status["rollback_hold"]:
            return False
        return self.config.refresh_on_start

    def _checkout(self, target: Path) -> str:
        target.mkdir(parents=True)
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ALLOW_PROTOCOL": "https",
        }
        commands = (
            ("git", "init", "--quiet", str(target)),
            ("git", "-C", str(target), "remote", "add", "origin", self.config.repository),
            (
                "git",
                "-C",
                str(target),
                "fetch",
                "--quiet",
                "--depth=1",
                "--no-tags",
                "--filter=blob:limit=16m",
                "origin",
                self.config.ref,
            ),
            ("git", "-C", str(target), "checkout", "--quiet", "--detach", "FETCH_HEAD"),
        )
        for command in commands:
            completed = subprocess.run(
                command,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if completed.returncode != 0:
                message = completed.stderr.strip() or completed.stdout.strip() or "git failed"
                raise ContentDeploymentError(f"public Git checkout failed: {message}")
        completed = subprocess.run(
            ("git", "-C", str(target), "rev-parse", "HEAD"),
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        resolved = completed.stdout.strip().lower()
        if _SHA_PATTERN.fullmatch(resolved) is None:
            raise ContentDeploymentError("public Git checkout did not resolve to a full commit")
        return resolved

    def _validate_source(self, source: Path) -> tuple[int, int]:
        if (source / ".gitmodules").exists():
            raise ContentDeploymentError("Git submodules are not supported in adopter content")
        total = 0
        files = 0
        for path in source.rglob("*"):
            if ".git" in path.relative_to(source).parts:
                continue
            if path.is_symlink():
                raise ContentDeploymentError(f"content symlinks are not allowed: {path}")
            if not path.is_file():
                continue
            files += 1
            total += path.stat().st_size
            if files > self.config.max_files:
                raise ContentDeploymentError(
                    f"content exceeds FURA_CONTENT_MAX_FILES={self.config.max_files}"
                )
            if total > self.config.max_bytes:
                raise ContentDeploymentError(
                    f"content exceeds FURA_CONTENT_MAX_BYTES={self.config.max_bytes}"
                )
        return total, files

    def _freeze(self, app_root: Path, output: Path) -> Mapping[str, Any]:
        from furatena.catalog.freeze import FreezeCatalogOptions, freeze_catalog

        result = freeze_catalog(
            FreezeCatalogOptions(
                docs_config=app_root / "docs.yaml",
                app_root=app_root,
                repo_root=app_root.parent,
                output_dir=output,
                full_rebuild=True,
                workers=1,
                autodoc=False,
            )
        )
        return {"page_count": result.page_count, "frozen_root": result.output_dir}

    @staticmethod
    def _validate_frozen(output: Path, freeze: Mapping[str, Any]) -> None:
        root = Path(str(freeze.get("frozen_root") or output))
        required = ("catalog.json", "search.json", "semantic.json", "llms-full.txt")
        missing = [name for name in required if not (root / name).is_file()]
        if missing:
            raise ContentDeploymentError(
                f"frozen generation is incomplete; missing {', '.join(missing)}"
            )
        if int(freeze.get("page_count") or 0) < 1:
            raise ContentDeploymentError("frozen generation must contain at least one page")

    def _generation_id(self, resolved_ref: str, timestamp: float) -> str:
        return f"{datetime.fromtimestamp(timestamp, tz=UTC).strftime('%Y%m%dT%H%M%SZ')}-{resolved_ref[:12]}"

    def _link_target(self, link: Path) -> Path | None:
        if not link.is_symlink():
            return None
        try:
            target = (link.parent / os.readlink(link)).resolve()
        except OSError:
            return None
        if not target.is_relative_to(self.generations.resolve()) or not self._generation_valid(
            target
        ):
            return None
        return target

    @staticmethod
    def _generation_valid(generation: Path) -> bool:
        return (
            generation.is_dir()
            and (generation / "receipt.json").is_file()
            and (generation / "frozen" / "catalog.json").is_file()
            and (generation / "source").is_dir()
        )

    def _replace_link(self, link: Path, target: Path) -> None:
        if not target.is_relative_to(self.generations.resolve()) or not self._generation_valid(
            target
        ):
            raise ContentDeploymentError(f"refusing to select invalid generation: {target}")
        link.parent.mkdir(parents=True, exist_ok=True)
        temporary = link.parent / f".{link.name}.{uuid.uuid4().hex}.tmp"
        temporary.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)
        os.replace(temporary, link)

    def _read_state(self) -> dict[str, Any]:
        return _read_json(self.state / "current.json") or {}

    def _active_receipt(self) -> dict[str, Any] | None:
        active = self._link_target(self.active)
        return _read_json(active / "receipt.json") if active is not None else None

    def _set_rollback_hold(self, active: bool) -> None:
        state = self._read_state()
        if not state:
            return
        state["rollback_hold"] = active
        state["updated_at"] = _iso(self._clock())
        _write_json(self.state / "current.json", state)

    def _prune_generations(self, *, keep: int) -> None:
        protected = {
            target
            for link in (self.active, self.last_known_good)
            if (target := self._link_target(link)) is not None
        }
        generations = sorted(
            (path for path in self.generations.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        retained = 0
        for generation in generations:
            if generation in protected or retained < keep:
                retained += 1
                continue
            shutil.rmtree(generation, ignore_errors=True)


def refresh_authorized(
    authorization: str | None,
    body: bytes,
    signature: str | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Authenticate a refresh with either an independent bearer or webhook secret."""
    values = os.environ if environ is None else environ
    token = values.get("FURA_CONTENT_REFRESH_TOKEN", "").strip()
    provided = (authorization or "").removeprefix("Bearer ").strip()
    if token and len(token) >= 32 and hmac.compare_digest(provided, token):
        return True
    secret = values.get("FURA_CONTENT_WEBHOOK_SECRET", "").strip()
    if not secret or len(secret) < 32 or not signature:
        return False
    provided_signature = signature.removeprefix("sha256=").strip().lower()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided_signature, expected)


def _positive_int(value: str, label: str) -> int:
    try:
        normalized = int(value)
    except ValueError as exc:
        raise ContentDeploymentError(
            f"FURA_CONTENT_MAX_{label.upper()} must be an integer"
        ) from exc
    if normalized < 1:
        raise ContentDeploymentError(f"FURA_CONTENT_MAX_{label.upper()} must be positive")
    return normalized


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
