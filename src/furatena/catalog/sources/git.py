"""Git source syncing for mount-backed docs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

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
) -> GitSyncResult:
    """Clone/fetch a git source and return the local content root."""
    base = _sync_base(config, app_root)
    if cache_namespace:
        base = base / cache_namespace
    repo_root = base / mount_id / "repo"
    repo_root.parent.mkdir(parents=True, exist_ok=True)

    if not (repo_root / ".git").is_dir():
        _run_git("clone", config.repo, str(repo_root))
    else:
        _run_git("-C", str(repo_root), "fetch", "--all", "--tags", "--prune")

    _run_git("-C", str(repo_root), "checkout", "--force", config.ref)
    resolved_ref = _run_git("-C", str(repo_root), "rev-parse", "HEAD").strip()
    content_root = (repo_root / config.path).resolve() if config.path else repo_root.resolve()
    if not content_root.is_dir():
        raise RuntimeError(
            f"git source path for mount {mount_id!r} does not exist: {config.path or '.'}"
        )
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


def _run_git(*args: str) -> str:
    try:
        result = subprocess.run(
            ("git", *args),
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git executable is required for git-backed mounts") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        command = "git " + " ".join(args)
        raise RuntimeError(f"{command} failed: {detail}") from exc
    return result.stdout
