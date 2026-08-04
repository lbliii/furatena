"""Durable public-Git content generations for proprietary image deployments."""

from __future__ import annotations

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

from furatena.catalog.content_generation import (
    GenerationContractError,
    prepare_generation_contract,
    verify_generation_contract,
)
from furatena.catalog.operation_lease import (
    OperationLease,
    operation_lease_seconds,
    operation_timeout_seconds,
)

_TRUE = frozenset({"1", "true", "yes", "on"})
_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,254}$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_UNSET = object()
_PROTECTED_MANAGED_PATHS = frozenset(
    {
        ".docs-cache",
        ".preview",
        "active",
        "frozen",
        "generations",
        "last-known-good",
        "leases",
        "operations",
        "quarantine",
        "receipts",
        "staging",
        "state",
    }
)


class ContentDeploymentError(RuntimeError):
    """A content generation could not be safely prepared or selected."""


class ContentDeploymentConflict(ContentDeploymentError):
    """A deterministic request conflict that must not mutate active content."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ContentDeploymentValidationError(ContentDeploymentError):
    """A staged generation failed a named pre-promotion validation gate."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


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
        if (
            not subdirectory
            or subpath.is_absolute()
            or ".." in subpath.parts
            or any(part in _PROTECTED_MANAGED_PATHS for part in subpath.parts)
        ):
            raise ContentDeploymentError(
                "FURA_CONTENT_SUBDIRECTORY must name a safe relative application path "
                "outside protected managed namespaces."
            )
        state_path = Path(values.get("FURA_CONTENT_STATE_ROOT", "/data/furatena")).expanduser()
        if not state_path.is_absolute() or state_path.is_symlink():
            raise ContentDeploymentError(
                "The configured FURA_CONTENT_STATE_ROOT must be an absolute non-symlink path."
            )
        state_root = state_path.resolve()
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


@dataclass(frozen=True, slots=True)
class GenerationSelection:
    """One immutable checkout/site/frozen/receipt selection."""

    generation: str
    generation_root: Path
    checkout_root: Path
    site_root: Path
    frozen_root: Path
    receipt_path: Path

    @classmethod
    def from_generation(cls, generation_root: Path) -> GenerationSelection:
        root = generation_root.resolve()
        receipt_path = root / "receipt.json"
        receipt = _read_json(receipt_path)
        if receipt is None:
            raise ContentDeploymentError(
                f"The managed generation receipt is missing or invalid: {receipt_path}."
            )
        generation = str(receipt.get("generation") or root.name)
        if generation != root.name:
            raise ContentDeploymentError(
                f"The managed generation receipt identity does not match its directory: {root}."
            )
        selection = receipt.get("selection")
        if not isinstance(selection, dict):
            selection = {
                "checkout": "source",
                "site": f"source/{str(receipt.get('subdirectory') or 'app').strip('/')}",
                "frozen": "frozen",
                "receipt": "receipt.json",
            }
        paths = {
            label: _selection_path(root, selection.get(label), label=label)
            for label in ("checkout", "site", "frozen", "receipt")
        }
        if not paths["checkout"].is_dir() or not paths["site"].is_dir():
            raise ContentDeploymentError(
                f"The managed generation checkout and site selection is incomplete: {root}."
            )
        if not (paths["site"] / "docs.yaml").is_file():
            raise ContentDeploymentError(
                "The managed generation site selection does not contain the required docs.yaml: "
                f"{paths['site']}."
            )
        if not (paths["frozen"] / "catalog.json").is_file():
            raise ContentDeploymentError(
                f"The managed generation frozen artifact selection is incomplete: {paths['frozen']}."
            )
        if paths["receipt"] != receipt_path:
            raise ContentDeploymentError(
                "The managed generation receipt selection must resolve to receipt.json."
            )
        return cls(
            generation=generation,
            generation_root=root,
            checkout_root=paths["checkout"],
            site_root=paths["site"],
            frozen_root=paths["frozen"],
            receipt_path=receipt_path,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "generation": self.generation,
            "checkout_root": str(self.checkout_root),
            "site_root": str(self.site_root),
            "frozen_root": str(self.frozen_root),
            "receipt_path": str(self.receipt_path),
        }

    def require_runtime(self, *, site_root: Path, frozen_root: Path) -> None:
        if site_root.resolve() != self.site_root or frozen_root.resolve() != self.frozen_root:
            raise ContentDeploymentError(
                "The managed runtime roots do not match the active generation receipt; "
                "reconcile content and restart with the selected site and frozen roots."
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
        self.quarantine = self.root / "quarantine"
        self._freezer = freezer or self._freeze
        self._clock = clock

    def refresh(
        self,
        *,
        trigger: str = "manual",
        expected_active_commit: str | None | object = _UNSET,
        requested_commit: str | None = None,
        actor: str | None = None,
        operation_id: str | None = None,
        semantic_digest: str | None = None,
        idempotency_key_digest: str | None = None,
    ) -> dict[str, Any]:
        """Build and atomically activate one immutable generation."""
        with OperationLease(
            self.leases,
            "content-refresh",
            resource=self.config.repository,
            timeout_seconds=operation_timeout_seconds(),
            lease_seconds=operation_lease_seconds(),
        ):
            self.reconcile()
            current = self._active_receipt()
            current_commit = str(current.get("resolved_ref") or "") if current is not None else None
            if expected_active_commit is not _UNSET and current_commit != expected_active_commit:
                raise ContentDeploymentConflict(
                    code="stale_active_commit",
                    message="The expected active content commit no longer matches the selected generation.",
                )
            started = self._clock()
            attempt_id = uuid.uuid4().hex
            workspace = self.staging / attempt_id
            source = workspace / "source"
            frozen = workspace / "frozen"
            generation: Path | None = None
            promoted = False
            self.staging.mkdir(parents=True, exist_ok=True)
            try:
                resolved_ref = (
                    self._checkout(source, requested_commit=requested_commit)
                    if requested_commit is not None
                    else self._checkout(source)
                )
                if requested_commit is not None and resolved_ref != requested_commit:
                    raise ContentDeploymentConflict(
                        code="unreachable_commit",
                        message="The exact requested commit is not reachable under the configured ref policy.",
                    )
                source_bytes, source_files = self._validate_source(source)
                image_digest = os.environ.get("FURA_IMAGE_DIGEST", "unknown").strip() or "unknown"
                build_commit = (
                    os.environ.get("FURA_BUILD_GIT_SHA")
                    or os.environ.get("RAILWAY_GIT_COMMIT_SHA")
                    or "unknown"
                ).strip() or "unknown"
                if (
                    current is not None
                    and current.get("resolved_ref") == resolved_ref
                    and current.get("image_digest") == image_digest
                    and current.get("build_commit", "unknown") == build_commit
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
                    "schema_version": 2,
                    "generation": generation_id,
                    "status": "active",
                    "trigger": trigger,
                    "repository": self.config.repository,
                    "requested_ref": self.config.ref,
                    "requested_commit": requested_commit,
                    "expected_active_commit": (
                        expected_active_commit if expected_active_commit is not _UNSET else None
                    ),
                    "resolved_ref": resolved_ref,
                    "image_digest": image_digest,
                    "build_commit": build_commit,
                    "actor": actor,
                    "operation_id": operation_id,
                    "semantic_digest": semantic_digest,
                    "idempotency_key_digest": idempotency_key_digest,
                    "subdirectory": self.config.subdirectory,
                    "source_bytes": source_bytes,
                    "source_files": source_files,
                    "page_count": int(freeze.get("page_count") or 0),
                    "frozen_root": str(freeze.get("frozen_root") or frozen),
                    "started_at": _iso(started),
                    "promoted_at": _iso(self._clock()),
                    "selection": {
                        "checkout": "source",
                        "site": f"source/{self.config.subdirectory}",
                        "frozen": "frozen",
                        "receipt": "receipt.json",
                    },
                    "generation_contract_version": 1,
                }
                workspace_receipt = workspace / "receipt.json"
                _write_json(workspace_receipt, receipt)
                operation_receipt = {
                    key: value
                    for key, value in {
                        "operation_id": operation_id,
                        "semantic_digest": semantic_digest,
                        "idempotency_key_digest": idempotency_key_digest,
                    }.items()
                    if value is not None
                }
                try:
                    _manifest, verification = prepare_generation_contract(
                        workspace,
                        source_root=source,
                        app_root=app_root,
                        frozen_root=frozen,
                        generation=generation_id,
                        repository=self.config.repository,
                        requested_ref=self.config.ref,
                        requested_commit=requested_commit,
                        resolved_commit=resolved_ref,
                        image_digest=image_digest,
                        build_commit=build_commit,
                        actor=actor or "legacy-local",
                        operation=operation_receipt,
                        page_count=int(freeze.get("page_count") or 0),
                        verified_at=str(receipt["promoted_at"]),
                        max_files=self.config.max_files,
                        max_bytes=self.config.max_bytes,
                    )
                except GenerationContractError as exc:
                    raise ContentDeploymentValidationError(exc.code, str(exc)) from exc
                receipt["manifest_digest"] = verification["manifest_digest"]
                receipt["verification_status"] = verification["status"]
                _write_json(workspace_receipt, receipt)
                self.generations.mkdir(parents=True, exist_ok=True)
                os.replace(workspace, generation)
                receipt["frozen_root"] = str(generation / "frozen")
                _write_json(generation / "receipt.json", receipt)
                try:
                    verify_generation_contract(generation, full=True, allow_legacy=False)
                except GenerationContractError as exc:
                    self._quarantine_generation(
                        generation,
                        code=exc.code,
                        message=str(exc),
                    )
                    generation = None
                    raise ContentDeploymentValidationError(exc.code, str(exc)) from exc
                selection = GenerationSelection.from_generation(generation)
                _make_tree_read_only(generation)
                previous = self._link_target(self.active)
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
                if previous is not None:
                    self._replace_link(self.last_known_good, previous)
                self._replace_link(self.active, generation)
                promoted = True
                return {**receipt, "generation_selection": selection.to_dict()}
            except BaseException as exc:
                failure = {
                    "schema_version": 1,
                    "attempt": attempt_id,
                    "status": "failed",
                    "trigger": trigger,
                    "repository": self.config.repository,
                    "requested_ref": self.config.ref,
                    "requested_commit": requested_commit,
                    "expected_active_commit": (
                        expected_active_commit if expected_active_commit is not _UNSET else None
                    ),
                    "failed_at": _iso(self._clock()),
                    "error": str(exc) or exc.__class__.__name__,
                }
                self.receipts.mkdir(parents=True, exist_ok=True)
                _write_json(self.receipts / f"failed-{attempt_id}.json", failure)
                if generation is not None and generation.is_dir() and not promoted:
                    self._quarantine_generation(
                        generation,
                        code=getattr(exc, "code", "promotion_failed"),
                        message=str(exc) or exc.__class__.__name__,
                    )
                elif workspace.is_dir():
                    _write_json(workspace / "failure.json", failure)
                    preserved = self.staging / f"failed-{attempt_id}"
                    os.replace(workspace, preserved)
                    self._prune_failed_staging(keep=3)
                raise
            finally:
                shutil.rmtree(workspace, ignore_errors=True)

    def reconcile(self) -> dict[str, Any]:
        """Repair safe crash residue and report the selected generation."""
        for workspace in self.staging.glob("*") if self.staging.is_dir() else ():
            if workspace.is_dir() and not workspace.name.startswith("failed-"):
                shutil.rmtree(workspace, ignore_errors=True)
        active, active_quarantine = self._reconcile_link(self.active)
        lkg, lkg_quarantine = self._reconcile_link(self.last_known_good)
        quarantine_event = active_quarantine or lkg_quarantine
        repaired = False
        if active is None and lkg is not None:
            self._replace_link(self.active, lkg)
            active = lkg
            repaired = True
        if active is None:
            state = self._read_state()
            candidate = self.generations / str(state.get("active_generation") or "")
            if candidate.is_dir():
                error = self._generation_error(candidate)
                if error is None:
                    self._replace_link(self.active, candidate)
                    active = candidate
                    repaired = True
                else:
                    quarantine_event = self._quarantine_generation(
                        candidate,
                        code=error[0],
                        message=error[1],
                    )
        return {
            "configured": True,
            "status": "ready" if active is not None else "uninitialized",
            "active_generation": active.name if active is not None else None,
            "last_known_good_generation": lkg.name if lkg is not None else None,
            "repaired": repaired,
            "rollback_hold": bool(self._read_state().get("rollback_hold")),
            "generation_quarantine": quarantine_event or self._latest_quarantine(),
        }

    def rollback(
        self,
        *,
        actor: str = "legacy-local",
        reason: str = "Legacy rollback request.",
    ) -> dict[str, Any]:
        """Atomically select last-known-good and retain the prior active generation."""
        with OperationLease(
            self.leases,
            "content-refresh",
            resource=self.config.repository,
            timeout_seconds=operation_timeout_seconds(),
            lease_seconds=operation_lease_seconds(),
        ):
            active, active_quarantine = self._reconcile_link(self.active)
            lkg, lkg_quarantine = self._reconcile_link(self.last_known_good)
            if lkg is None:
                quarantine = lkg_quarantine or active_quarantine
                if quarantine is not None:
                    raise ContentDeploymentConflict(
                        code=str(quarantine.get("code") or "generation_manifest_invalid"),
                        message=(
                            "The requested rollback generation failed integrity verification and "
                            f"was quarantined as {quarantine.get('generation') or 'unknown'}."
                        ),
                    )
                raise ContentDeploymentError(
                    "No valid last-known-good content generation is available for rollback."
                )
            image_digest = os.environ.get("FURA_IMAGE_DIGEST", "unknown").strip() or "unknown"
            build_commit = (
                os.environ.get("FURA_BUILD_GIT_SHA")
                or os.environ.get("RAILWAY_GIT_COMMIT_SHA")
                or "unknown"
            ).strip() or "unknown"
            try:
                verification = verify_generation_contract(
                    lkg,
                    full=True,
                    allow_legacy=True,
                    image_digest=image_digest,
                    build_commit=build_commit,
                )
            except GenerationContractError as exc:
                quarantine = self._quarantine_generation(
                    lkg,
                    code=exc.code,
                    message=str(exc),
                )
                raise ContentDeploymentConflict(
                    code=exc.code,
                    message=(
                        "The requested rollback generation failed integrity verification and "
                        f"was quarantined as {quarantine['generation'] if quarantine else 'unknown'}."
                    ),
                ) from exc
            self._replace_link(self.active, lkg)
            if active is not None:
                self._replace_link(self.last_known_good, active)
            receipt = {
                "schema_version": 1,
                "operation": "rollback",
                "status": "active",
                "active_generation": lkg.name,
                "last_known_good_generation": active.name if active else None,
                "actor": _required_text(actor, "rollback actor"),
                "reason": _required_text(reason, "rollback reason"),
                "generation_verification": verification,
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

    def status(self, *, full_verification: bool = False) -> dict[str, Any]:
        status = self.reconcile()
        active = self._link_target(self.active)
        status["repository"] = self.config.repository
        status["requested_ref"] = self.config.ref
        status["subdirectory"] = self.config.subdirectory
        status["receipt"] = _read_json(active / "receipt.json") if active is not None else None
        status["generation_selection"] = (
            GenerationSelection.from_generation(active).to_dict() if active is not None else None
        )
        if active is not None:
            try:
                status["generation_verification"] = verify_generation_contract(
                    active, full=full_verification, allow_legacy=True
                )
            except GenerationContractError as exc:
                status["generation_verification"] = {
                    "status": "degraded",
                    "code": exc.code,
                    "verified": False,
                    "compatible": False,
                }
        else:
            status["generation_verification"] = None
        return status

    def active_selection(self) -> GenerationSelection:
        """Return the one receipt-bound generation selected for serving."""
        active = self._link_target(self.active)
        if active is None:
            raise ContentDeploymentError(
                "No valid managed content generation is active; refresh or reconcile content first."
            )
        image_digest = os.environ.get("FURA_IMAGE_DIGEST", "unknown").strip() or "unknown"
        build_commit = (
            os.environ.get("FURA_BUILD_GIT_SHA")
            or os.environ.get("RAILWAY_GIT_COMMIT_SHA")
            or "unknown"
        ).strip() or "unknown"
        try:
            verify_generation_contract(
                active,
                full=True,
                allow_legacy=True,
                image_digest=image_digest,
                build_commit=build_commit,
            )
        except GenerationContractError as exc:
            raise ContentDeploymentValidationError(exc.code, str(exc)) from exc
        return GenerationSelection.from_generation(active)

    def startup_refresh_needed(self) -> bool:
        """Return whether startup may follow the desired ref without undoing rollback."""
        status = self.reconcile()
        if status["active_generation"] is None:
            return True
        if status["rollback_hold"]:
            return False
        return self.config.refresh_on_start

    def resolve_requested_commit(self, requested_commit: str) -> str:
        """Resolve one exact commit under configured ref policy without promotion."""
        commit = _full_commit(requested_commit, "requested_commit")
        workspace = self.staging / f"resolve-{uuid.uuid4().hex}"
        try:
            return self._checkout(workspace, requested_commit=commit)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _checkout(self, target: Path, *, requested_commit: str | None = None) -> str:
        target.mkdir(parents=True)
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ALLOW_PROTOCOL": "https",
        }
        if requested_commit is not None:
            requested_commit = _full_commit(requested_commit, "requested_commit")
        commands = (
            ("git", "init", "--quiet", str(target)),
            ("git", "-C", str(target), "remote", "add", "origin", self.config.repository),
            (
                "git",
                "-c",
                "http.followRedirects=false",
                "-C",
                str(target),
                "fetch",
                "--quiet",
                "--no-tags",
                "--filter=blob:limit=16m",
                "origin",
                self.config.ref,
            ),
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
        if requested_commit is not None:
            reachable = subprocess.run(
                (
                    "git",
                    "-C",
                    str(target),
                    "merge-base",
                    "--is-ancestor",
                    requested_commit,
                    "FETCH_HEAD",
                ),
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if reachable.returncode != 0:
                raise ContentDeploymentConflict(
                    code="unreachable_commit",
                    message="The exact requested commit is not reachable under the configured ref policy.",
                )
        checkout_target = requested_commit or "FETCH_HEAD"
        completed = subprocess.run(
            ("git", "-C", str(target), "checkout", "--quiet", "--detach", checkout_target),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if completed.returncode != 0:
            raise ContentDeploymentConflict(
                code="unreachable_commit",
                message="The exact requested commit is not reachable under the configured ref policy.",
            )
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
                platform_root=Path(os.environ.get("FURA_PLATFORM_ROOT", "/app/app"))
                .expanduser()
                .resolve(),
                state_root=self.root / "runtime-state" / "refresh",
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
        target = self._link_candidate(link)
        return target if target is not None and self._generation_valid(target) else None

    def _link_candidate(self, link: Path) -> Path | None:
        if not link.is_symlink():
            return None
        try:
            target = (link.parent / os.readlink(link)).resolve()
        except OSError:
            return None
        if not target.is_relative_to(self.generations.resolve()) or not target.is_dir():
            return None
        return target

    @staticmethod
    def _generation_valid(generation: Path) -> bool:
        return ContentDeploymentStore._generation_error(generation) is None

    @staticmethod
    def _generation_error(generation: Path) -> tuple[str, str] | None:
        if not generation.is_dir():
            return (
                "generation_selection_invalid",
                "The recorded content generation directory is unavailable.",
            )
        try:
            GenerationSelection.from_generation(generation)
            verify_generation_contract(generation, full=False, allow_legacy=True)
        except GenerationContractError as exc:
            return exc.code, str(exc)
        except ContentDeploymentError as exc:
            return "generation_selection_invalid", str(exc)
        return None

    def _reconcile_link(self, link: Path) -> tuple[Path | None, dict[str, Any] | None]:
        target = self._link_candidate(link)
        if target is None:
            return None, None
        error = self._generation_error(target)
        if error is None:
            return target, None
        return None, self._quarantine_generation(
            target,
            code=error[0],
            message=error[1],
        )

    def _quarantine_generation(
        self,
        generation: Path,
        *,
        code: str,
        message: str,
    ) -> dict[str, Any] | None:
        root = generation.resolve()
        generations_root = self.generations.resolve()
        if not root.is_dir() or not root.is_relative_to(generations_root):
            return None
        quarantine_id = f"{root.name}-{uuid.uuid4().hex}"
        self.quarantine.mkdir(parents=True, exist_ok=True)
        destination = self.quarantine / quarantine_id
        root.chmod(0o755)
        try:
            os.replace(root, destination)
        except BaseException:
            root.chmod(0o555)
            raise
        destination.chmod(0o555)
        recorded_at = _iso(self._clock())
        record = {
            "schema_version": 1,
            "record_type": "furatena.content-generation.quarantine",
            "generation": root.name,
            "quarantine_id": quarantine_id,
            "status": "quarantined",
            "code": _required_text(code, "quarantine code"),
            "message": (str(message or "Generation integrity verification failed.").strip())[:500],
            "recorded_at": recorded_at,
        }
        self.receipts.mkdir(parents=True, exist_ok=True)
        _write_json(self.receipts / f"quarantine-{quarantine_id}.json", record)
        self._prune_quarantine(keep=5)
        return record

    def _latest_quarantine(self) -> dict[str, Any] | None:
        records = [
            value
            for path in self.receipts.glob("quarantine-*.json")
            if (value := _read_json(path)) is not None
        ]
        if not records:
            return None
        latest = max(
            records,
            key=lambda value: (
                str(value.get("recorded_at") or ""),
                str(value.get("quarantine_id") or ""),
            ),
        )
        return {
            key: latest.get(key)
            for key in (
                "schema_version",
                "record_type",
                "generation",
                "quarantine_id",
                "status",
                "code",
                "recorded_at",
            )
        }

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
            _remove_read_only_tree(generation)

    def _prune_failed_staging(self, *, keep: int) -> None:
        failures = sorted(
            (
                path
                for path in self.staging.glob("failed-*")
                if path.is_dir() and not path.is_symlink()
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for failure in failures[keep:]:
            shutil.rmtree(failure, ignore_errors=True)

    def _prune_quarantine(self, *, keep: int) -> None:
        generations = sorted(
            (path for path in self.quarantine.iterdir() if path.is_dir() and not path.is_symlink()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for generation in generations[keep:]:
            _remove_read_only_tree(generation)


def refresh_authorized(
    authorization: str | None,
    body: bytes,
    signature: str | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Authenticate the v1 refresh bearer; webhook transport remains deferred."""
    del body, signature
    values = os.environ if environ is None else environ
    token = values.get("FURA_CONTENT_REFRESH_TOKEN", "").strip()
    scheme, separator, provided = (authorization or "").partition(" ")
    return bool(
        separator
        and scheme.casefold() == "bearer"
        and token
        and len(token) >= 32
        and hmac.compare_digest(provided.strip(), token)
    )


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


def _full_commit(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if _SHA_PATTERN.fullmatch(normalized) is None:
        raise ContentDeploymentConflict(
            code="invalid_commit",
            message=f"{label} must be an exact lowercase 40-character Git commit.",
        )
    return normalized


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ContentDeploymentError(f"{label} is required")
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


def _selection_path(root: Path, raw: object, *, label: str) -> Path:
    relative = Path(str(raw or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ContentDeploymentError(
            f"The managed generation {label} selection must use a safe relative path."
        )
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ContentDeploymentError(
            f"The managed generation {label} selection escapes its generation: {relative}."
        )
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ContentDeploymentError(
                f"The managed generation {label} selection cannot traverse a symbolic link: "
                f"{current}."
            )
    return resolved


def _make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            continue
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _remove_read_only_tree(root: Path) -> None:
    for path in root.rglob("*") if root.is_dir() else ():
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o644)
    if root.exists():
        root.chmod(0o755)
    shutil.rmtree(root, ignore_errors=True)
