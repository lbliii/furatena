"""Durable source-sync retry, quarantine, and reconciliation contracts."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.registry import CatalogRegistry
from furatena.catalog.source_sync_state import SourceSyncStateStore
from furatena.catalog.sources.git import sync_git_source
from furatena.catalog.sources.types import GitSourceConfig


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "remote"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Tests")
    docs = repo / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("---\ntitle: Version One\n---\n# One\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-m", "version one")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_retry_backoff_quarantine_and_reconciliation_survive_restart(tmp_path: Path) -> None:
    now = [100.0]
    root = tmp_path / "state"
    store = SourceSyncStateStore(root, clock=lambda: now[0], max_failures=3)

    store.begin("remote", provider="git", source_repo="repo", requested_ref="main")
    first = store.record_failure("remote", {"message": "network failed"})
    assert first["state"] == "retry"
    assert first["retry"]["backoff_seconds"] == 5.0
    assert store.can_attempt("remote") == (False, "backoff")

    now[0] = 105.0
    assert store.can_attempt("remote") == (True, None)
    store.begin("remote", provider="git", source_repo="repo", requested_ref="main")
    second = store.record_failure("remote", {"message": "auth failed", "token": "secret"})
    assert second["retry"]["backoff_seconds"] == 10.0
    assert second["last_error"]["token"] == "<redacted>"

    now[0] = 115.0
    store.begin("remote", provider="git", source_repo="repo", requested_ref="main")
    quarantined = store.record_failure("remote", {"message": "invalid ref"})
    assert quarantined["state"] == "quarantine"
    assert quarantined["quarantine"]["active"] is True

    restarted = SourceSyncStateStore(root, clock=lambda: now[0], max_failures=3)
    assert restarted.can_attempt("remote") == (False, "quarantined")
    cleared = restarted.clear_quarantine("remote")
    assert cleared["state"] == "retry"
    assert restarted.can_attempt("remote") == (True, None)

    reconciled = restarted.reconcile(
        "remote",
        provider="git",
        resolved_ref="abc123",
        content_root=tmp_path,
        source_url="https://example.test/repo",
    )
    assert reconciled["state"] == "reconciled"
    assert reconciled["consecutive_failures"] == 0
    assert reconciled["last_known_good"]["resolved_ref"] == "abc123"
    assert {item["state"] for item in reconciled["history"]} >= {
        "attempt",
        "error",
        "retry",
        "quarantine",
        "reconciled",
    }


def test_failed_git_sync_never_replaces_last_known_good_snapshot(tmp_path: Path) -> None:
    repo, first_ref = _repo(tmp_path)
    app_root = tmp_path / "app"
    config = GitSourceConfig(repo=str(repo), ref=first_ref, path="docs")
    first = sync_git_source(config, mount_id="remote", app_root=app_root)
    active_source = first.content_root / "guide.md"
    original = active_source.read_text(encoding="utf-8")

    def reject_candidate(_path: Path) -> None:
        raise ValueError("candidate failed validation")

    with pytest.raises(ValueError, match="candidate failed validation"):
        sync_git_source(
            config,
            mount_id="remote",
            app_root=app_root,
            validate=reject_candidate,
        )
    assert active_source.read_text(encoding="utf-8") == original
    assert _git(first.repo_root, "rev-parse", "HEAD") == first_ref

    with pytest.raises(RuntimeError, match="missing-ref"):
        sync_git_source(
            GitSourceConfig(repo=str(repo), ref="missing-ref", path="docs"),
            mount_id="remote",
            app_root=app_root,
        )

    assert active_source.read_text(encoding="utf-8") == original
    assert _git(first.repo_root, "rev-parse", "HEAD") == first_ref
    assert not list(first.repo_root.parent.glob(".repo-sync-*"))
    assert not list(first.repo_root.parent.glob(".repo-backup-*"))


def test_registry_serves_last_known_good_during_backoff(tmp_path: Path) -> None:
    repo, first_ref = _repo(tmp_path)
    app_root = tmp_path / "app"
    app_root.mkdir()
    mounts = app_root / "mounts.yaml"
    mounts.write_text(
        f"""mounts:
  - id: remote
    source:
      provider: git
      repo: {repo}
      ref: {first_ref}
      path: docs
""",
        encoding="utf-8",
    )
    first = CatalogRegistry.from_config(
        mounts,
        repo_root=tmp_path,
        app_root=app_root,
        autodoc=False,
    )
    assert first.get_by_slug("guide", mount="remote").title == "Version One"

    mounts.write_text(
        mounts.read_text(encoding="utf-8").replace(first_ref, "missing-ref"),
        encoding="utf-8",
    )
    failed = CatalogRegistry.from_config(
        mounts,
        repo_root=tmp_path,
        app_root=app_root,
        autodoc=False,
    )
    failed_health = failed.source_health()["mounts"][0]
    assert failed.get_by_slug("guide", mount="remote").title == "Version One"
    assert failed_health["status"] == "degraded"
    assert failed_health["source"]["sync_state"]["state"] == "retry"
    assert failed_health["source"]["repair_actions"]

    waiting = CatalogRegistry.from_config(
        mounts,
        repo_root=tmp_path,
        app_root=app_root,
        autodoc=False,
    )
    waiting_health = waiting.source_health()["mounts"][0]
    assert waiting.get_by_slug("guide", mount="remote").title == "Version One"
    assert waiting_health["source"]["status"] == "retry_wait"
    assert waiting_health["source"]["sync_state"]["last_known_good"]["resolved_ref"] == first_ref


def test_state_files_are_free_threading_safe(tmp_path: Path) -> None:
    assert_free_threading()
    store = SourceSyncStateStore(tmp_path / "state")

    def reconcile(index: int) -> str:
        mount = f"mount-{index}"
        store.begin(mount, provider="git", source_repo="repo", requested_ref="main")
        return store.reconcile(
            mount,
            provider="git",
            resolved_ref=f"ref-{index}",
            content_root=tmp_path,
            source_url=None,
        )["state"]

    with ThreadPoolExecutor(max_workers=64) as pool:
        states = list(pool.map(reconcile, range(256)))

    restarted = SourceSyncStateStore(tmp_path / "state")
    assert set(states) == {"reconciled"}
    assert all(restarted.load(f"mount-{index}")["state"] == "reconciled" for index in range(256))
