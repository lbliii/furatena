"""Git source syncing for mount-backed docs."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from furatena.catalog.exceptions import SourceSyncError
from furatena.catalog.operation_lease import (
    OperationLease,
    operation_lease_seconds,
    operation_timeout_seconds,
)
from furatena.catalog.sources.types import GitSourceConfig


@dataclass(frozen=True, slots=True)
class GitSyncResult:
    """Resolved local snapshot for a git-backed mount."""

    content_root: Path
    repo_root: Path
    resolved_ref: str
    source_url: str | None


def sync_git_source(
    config: GitSourceConfig,
    *,
    mount_id: str,
    app_root: Path,
    cache_namespace: str = "",
    validate: Callable[[Path], None] | None = None,
) -> GitSyncResult:
    """Clone/fetch a git source and return the local content root."""
    base = _sync_base(config, app_root)
    if cache_namespace:
        base = base / cache_namespace
    mount_root = base / mount_id
    with OperationLease(
        mount_root / ".operation-leases",
        "source-sync",
        resource=mount_id,
        timeout_seconds=operation_timeout_seconds(),
        lease_seconds=operation_lease_seconds(),
    ):
        return _sync_git_source_locked(
            config,
            mount_id=mount_id,
            app_root=app_root,
            cache_namespace=cache_namespace,
            validate=validate,
        )


def _sync_git_source_locked(
    config: GitSourceConfig,
    *,
    mount_id: str,
    app_root: Path,
    cache_namespace: str,
    validate: Callable[[Path], None] | None,
) -> GitSyncResult:
    base = _sync_base(config, app_root)
    if cache_namespace:
        base = base / cache_namespace
    mount_root = base / mount_id
    repo_root = mount_root / "repo"
    mount_root.mkdir(parents=True, exist_ok=True)
    staging = mount_root / f".repo-sync-{uuid.uuid4().hex}"
    backup = mount_root / f".repo-backup-{uuid.uuid4().hex}"
    try:
        if (repo_root / ".git").is_dir():
            _run_git("clone", "--no-hardlinks", str(repo_root), str(staging), mount_id=mount_id)
            _run_git(
                "-C",
                str(staging),
                "remote",
                "set-url",
                "origin",
                config.repo,
                mount_id=mount_id,
            )
        else:
            _run_git("clone", config.repo, str(staging), mount_id=mount_id)
        _run_git(
            "-C", str(staging), "fetch", "--all", "--tags", "--prune", mount_id=mount_id
        )
        _run_git("-C", str(staging), "checkout", "--force", config.ref, mount_id=mount_id)
        resolved_ref = _run_git(
            "-C", str(staging), "rev-parse", "HEAD", mount_id=mount_id
        ).strip()
        staged_content = (staging / config.path).resolve() if config.path else staging.resolve()
        if not staged_content.is_dir():
            raise SourceSyncError(
                f"git source path for mount {mount_id!r} does not exist: {config.path or '.'}",
                path=staged_content,
                mount=mount_id,
                operation="resolve_path",
            )
        if validate is not None:
            validate(staged_content)
        if repo_root.exists():
            os.replace(repo_root, backup)
        try:
            os.replace(staging, repo_root)
        except BaseException:
            if backup.exists() and not repo_root.exists():
                os.replace(backup, repo_root)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and repo_root.exists():
            shutil.rmtree(backup, ignore_errors=True)
    content_root = (repo_root / config.path).resolve() if config.path else repo_root.resolve()
    return GitSyncResult(
        content_root=content_root,
        repo_root=repo_root.resolve(),
        resolved_ref=resolved_ref,
        source_url=repo_web_url(config.repo),
    )


def repo_web_url(repo: str) -> str | None:
    """Return a browser/source URL for a repository value when possible."""
    value = repo.strip()
    if not value:
        return None
    if value.startswith("git@github.com:"):
        value = value.removeprefix("git@github.com:")
        return f"https://github.com/{value.removesuffix('.git')}"
    if value.startswith("https://github.com/") or value.startswith("http://github.com/"):
        return value.removesuffix(".git")
    path = Path(value)
    if path.exists():
        return path.resolve().as_uri()
    return value.removesuffix(".git")


def _sync_base(config: GitSourceConfig, app_root: Path) -> Path:
    if config.sync_root:
        root = Path(config.sync_root).expanduser()
        return root if root.is_absolute() else (app_root / root).resolve()
    return (app_root / ".docs-cache" / "sources").resolve()


def _run_git(*args: str, mount_id: str | None = None) -> str:
    try:
        result = subprocess.run(
            ("git", *args),
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SourceSyncError(
            "git executable is required for git-backed mounts",
            mount=mount_id,
            operation="git",
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        command = "git " + " ".join(args)
        raise SourceSyncError(
            f"{command} failed: {detail}",
            mount=mount_id,
            operation=command,
        ) from exc
    return result.stdout
