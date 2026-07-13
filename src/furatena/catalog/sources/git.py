"""Git source syncing for mount-backed docs."""

from __future__ import annotations

import fnmatch
import io
import os
import re
import shutil
import subprocess
import tarfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from furatena.catalog.exceptions import SourceSyncError
from furatena.catalog.operation_lease import (
    OperationLease,
    operation_lease_seconds,
    operation_timeout_seconds,
)
from furatena.catalog.sources.types import (
    GitEditionPolicy,
    GitEditionSnapshot,
    GitSourceConfig,
    validate_edition_id,
)

_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)"
    r"(?:\.(?P<minor>0|[1-9]\d*))?"
    r"(?:\.(?P<patch>0|[1-9]\d*))?"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


@dataclass(frozen=True, slots=True)
class GitSyncResult:
    """Resolved local snapshot for a git-backed mount."""

    content_root: Path
    repo_root: Path
    resolved_ref: str
    source_url: str | None
    editions: tuple[GitEditionSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class _EditionCandidate:
    id: str
    ref: str
    resolved_ref: str
    prerelease: bool
    semver: tuple[int, int, int, str] | None


def sync_git_source(
    config: GitSourceConfig,
    *,
    mount_id: str,
    app_root: Path,
    cache_namespace: str = "",
    validate: Callable[[Path], None] | None = None,
    edition_policy: GitEditionPolicy | None = None,
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
            edition_policy=edition_policy,
        )


def _sync_git_source_locked(
    config: GitSourceConfig,
    *,
    mount_id: str,
    app_root: Path,
    cache_namespace: str,
    validate: Callable[[Path], None] | None,
    edition_policy: GitEditionPolicy | None,
) -> GitSyncResult:
    base = _sync_base(config, app_root)
    if cache_namespace:
        base = base / cache_namespace
    mount_root = base / mount_id
    repo_root = mount_root / "repo"
    mount_root.mkdir(parents=True, exist_ok=True)
    staging = mount_root / f".repo-sync-{uuid.uuid4().hex}"
    backup = mount_root / f".repo-backup-{uuid.uuid4().hex}"
    editions_root = mount_root / "editions"
    editions_staging = mount_root / f".editions-sync-{uuid.uuid4().hex}"
    editions_backup = mount_root / f".editions-backup-{uuid.uuid4().hex}"
    snapshots: list[GitEditionSnapshot] = []
    discovered_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
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
        _run_git("-C", str(staging), "fetch", "--all", "--tags", "--prune", mount_id=mount_id)
        _run_git("-C", str(staging), "checkout", "--force", config.ref, mount_id=mount_id)
        resolved_ref = _run_git("-C", str(staging), "rev-parse", "HEAD", mount_id=mount_id).strip()
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
        candidates: tuple[_EditionCandidate, ...] = ()
        if edition_policy is not None:
            candidates = _discover_editions(
                staging,
                edition_policy,
                mount_id=mount_id,
            )
            editions_staging.mkdir(parents=True)
            for candidate in candidates:
                target = editions_staging / candidate.id
                _materialize_archive(
                    staging,
                    candidate.resolved_ref,
                    target,
                    mount_id=mount_id,
                )
                candidate_content = (target / config.path).resolve() if config.path else target
                if not candidate_content.is_dir():
                    raise SourceSyncError(
                        f"git source path for mount {mount_id!r} edition {candidate.id!r} "
                        f"does not exist: {config.path or '.'}",
                        path=candidate_content,
                        mount=mount_id,
                        operation="resolve_edition_path",
                    )
                if validate is not None:
                    validate(candidate_content)
            _retain_aged_snapshots(
                editions_root,
                editions_staging,
                active_ids={candidate.id for candidate in candidates},
            )
        repo_backed_up = False
        editions_backed_up = False
        repo_promoted = False
        editions_promoted = False
        try:
            if repo_root.exists():
                os.replace(repo_root, backup)
                repo_backed_up = True
            if edition_policy is not None and editions_root.exists():
                os.replace(editions_root, editions_backup)
                editions_backed_up = True
            os.replace(staging, repo_root)
            repo_promoted = True
            if edition_policy is not None:
                os.replace(editions_staging, editions_root)
                editions_promoted = True
        except BaseException:
            if editions_promoted and editions_root.exists():
                shutil.rmtree(editions_root, ignore_errors=True)
            if editions_backed_up and editions_backup.exists() and not editions_root.exists():
                os.replace(editions_backup, editions_root)
            if repo_promoted and repo_root.exists():
                shutil.rmtree(repo_root, ignore_errors=True)
            if repo_backed_up and backup.exists() and not repo_root.exists():
                os.replace(backup, repo_root)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(editions_backup, ignore_errors=True)
        if edition_policy is not None:
            snapshots.append(
                GitEditionSnapshot(
                    id="latest",
                    ref=config.ref,
                    resolved_ref=resolved_ref,
                    content_root=(repo_root / config.path).resolve()
                    if config.path
                    else repo_root.resolve(),
                    status="current",
                    prerelease=False,
                    discovered_at=discovered_at,
                )
            )
            for candidate in candidates:
                override = edition_policy.overrides.get(candidate.id)
                snapshots.append(
                    GitEditionSnapshot(
                        id=candidate.id,
                        ref=candidate.ref,
                        resolved_ref=candidate.resolved_ref,
                        content_root=(editions_root / candidate.id / config.path).resolve()
                        if config.path
                        else (editions_root / candidate.id).resolve(),
                        status=(
                            override.status
                            if override is not None
                            else ("preview" if candidate.prerelease else "legacy")
                        ),
                        prerelease=candidate.prerelease,
                        discovered_at=discovered_at,
                    )
                )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(editions_staging, ignore_errors=True)
        if backup.exists() and repo_root.exists():
            shutil.rmtree(backup, ignore_errors=True)
        if editions_backup.exists() and editions_root.exists():
            shutil.rmtree(editions_backup, ignore_errors=True)
    content_root = (repo_root / config.path).resolve() if config.path else repo_root.resolve()
    return GitSyncResult(
        content_root=content_root,
        repo_root=repo_root.resolve(),
        resolved_ref=resolved_ref,
        source_url=repo_web_url(config.repo),
        editions=tuple(snapshots),
    )


def _discover_editions(
    repo_root: Path,
    policy: GitEditionPolicy,
    *,
    mount_id: str,
) -> tuple[_EditionCandidate, ...]:
    tags = _run_git("-C", str(repo_root), "tag", "--list", mount_id=mount_id).splitlines()
    candidates: list[_EditionCandidate] = []
    normalized: dict[str, str] = {}
    for tag in tags:
        if not fnmatch.fnmatchcase(tag, policy.pattern):
            continue
        edition_id = tag.removeprefix(policy.strip_prefix) if policy.strip_prefix else tag
        try:
            validate_edition_id(edition_id, mount_id=mount_id)
        except ValueError as exc:
            raise SourceSyncError(str(exc), mount=mount_id, operation="discover_editions") from exc
        previous = normalized.get(edition_id)
        if previous is not None and previous != tag:
            raise SourceSyncError(
                f"mount {mount_id!r} edition normalization collision: tags {previous!r} "
                f"and {tag!r} both become {edition_id!r}; adjust strip_prefix or pattern",
                mount=mount_id,
                operation="discover_editions",
            )
        normalized[edition_id] = tag
        semver = _parse_semver(edition_id)
        if policy.sort == "semver-desc" and semver is None:
            raise SourceSyncError(
                f"mount {mount_id!r} tag {tag!r} is not semantic version syntax; "
                "narrow editions.pattern or use editions.sort: name-desc",
                mount=mount_id,
                operation="discover_editions",
            )
        prerelease = bool(semver and semver[3])
        if policy.sort == "semver-desc" and prerelease and not policy.include_prereleases:
            continue
        resolved_ref = _run_git(
            "-C",
            str(repo_root),
            "rev-parse",
            f"refs/tags/{tag}^{{commit}}",
            mount_id=mount_id,
        ).strip()
        candidates.append(
            _EditionCandidate(
                id=edition_id,
                ref=tag,
                resolved_ref=resolved_ref,
                prerelease=prerelease,
                semver=semver,
            )
        )
    if policy.sort == "semver-desc":
        candidates.sort(
            key=lambda item: (
                (
                    item.semver[0],
                    item.semver[1],
                    item.semver[2],
                    item.semver[3] == "",
                    item.semver[3],
                )
                if item.semver is not None
                else (-1, -1, -1, False, "")
            ),
            reverse=True,
        )
    else:
        candidates.sort(key=lambda item: item.ref, reverse=True)
    selected = list(candidates[: policy.count])
    selected_ids = {candidate.id for candidate in selected}
    by_id = {candidate.id: candidate for candidate in candidates}
    for edition_id, override in sorted(policy.overrides.items()):
        if override.status == "eol" or edition_id in selected_ids:
            continue
        candidate = by_id.get(edition_id)
        if candidate is None:
            raise SourceSyncError(
                f"mount {mount_id!r} edition override {edition_id!r} has no matching tag; "
                "correct the override id or editions.pattern",
                mount=mount_id,
                operation="discover_editions",
            )
        selected.append(candidate)
        selected_ids.add(edition_id)
    return tuple(selected)


def _parse_semver(value: str) -> tuple[int, int, int, str] | None:
    normalized = value[1:] if value.startswith(("v", "V")) else value
    match = _SEMVER_RE.fullmatch(normalized)
    if match is None:
        return None
    return (
        int(match.group("major")),
        int(match.group("minor") or 0),
        int(match.group("patch") or 0),
        match.group("prerelease") or "",
    )


def _materialize_archive(
    repo_root: Path,
    ref: str,
    target: Path,
    *,
    mount_id: str,
) -> None:
    archive = _run_git_bytes(
        "-C",
        str(repo_root),
        "archive",
        "--format=tar",
        ref,
        mount_id=mount_id,
    )
    target.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(target, filter="data")


def _retain_aged_snapshots(
    previous: Path,
    candidate: Path,
    *,
    active_ids: set[str],
) -> None:
    if not previous.is_dir():
        return
    retained = candidate / ".retained"
    previous_retained = previous / ".retained"
    if previous_retained.is_dir():
        shutil.copytree(previous_retained, retained)
    for child in previous.iterdir():
        if not child.is_dir() or child.name == ".retained" or child.name in active_ids:
            continue
        retained.mkdir(parents=True, exist_ok=True)
        destination = retained / child.name
        if not destination.exists():
            shutil.copytree(child, destination)


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


def _run_git_bytes(*args: str, mount_id: str | None = None) -> bytes:
    try:
        result = subprocess.run(
            ("git", *args),
            check=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise SourceSyncError(
            "git executable is required for git-backed mounts",
            mount=mount_id,
            operation="git",
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or b"").decode(errors="replace").strip()
        command = "git " + " ".join(args)
        raise SourceSyncError(
            f"{command} failed: {detail}",
            mount=mount_id,
            operation=command,
        ) from exc
    return result.stdout
