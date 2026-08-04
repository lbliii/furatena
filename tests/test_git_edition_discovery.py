"""Git-backed edition policy, discovery, and materialization contracts."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from furatena.catalog.channel_manifest import channel_manifest
from furatena.catalog.exceptions import SourceSyncError
from furatena.catalog.registry import CatalogRegistry, load_mounts
from furatena.catalog.source_sync_state import SourceSyncStateStore
from furatena.catalog.sources import git as git_source
from furatena.catalog.sources.git import sync_git_source
from furatena.catalog.sources.types import GitEditionPolicy, GitSourceConfig


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "remote"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Tests")
    (repo / "docs").mkdir()
    return repo


def _release(repo: Path, version: str, *, annotated: bool = False) -> str:
    (repo / "docs" / "guide.md").write_text(f"# {version}\n", encoding="utf-8")
    _git(repo, "add", "docs/guide.md")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-m", version)
    if annotated:
        _git(repo, "tag", "-a", f"v{version}", "-m", version)
    else:
        _git(repo, "tag", f"v{version}")
    return _git(repo, "rev-parse", "HEAD")


def _policy(**changes: object) -> GitEditionPolicy:
    raw = {
        "editions": {
            "source": "tags",
            "count": 3,
            "pattern": "v*",
            "strip_prefix": "v",
            "sort": "semver-desc",
            "include_prereleases": False,
            **changes,
        }
    }
    policy = GitEditionPolicy.from_mount_dict(raw, mount_id="docs", git_backed=True)
    assert policy is not None
    return policy


def test_latest_plus_three_editions_are_materialized_with_peeled_sha(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    commits = {
        version: _release(repo, version, annotated=version == "0.3.0")
        for version in ("0.1.0", "0.2.0", "0.3.0", "0.4.0")
    }

    result = sync_git_source(
        GitSourceConfig(repo=str(repo), ref="main", path="docs"),
        mount_id="docs",
        app_root=tmp_path / "app",
        edition_policy=_policy(),
    )

    assert [edition.id for edition in result.editions] == [
        "latest",
        "0.4.0",
        "0.3.0",
        "0.2.0",
    ]
    assert result.editions[2].resolved_ref == commits["0.3.0"]
    assert result.editions[2].content_root.joinpath("guide.md").read_text() == "# 0.3.0\n"
    assert result.editions[0].status == "current"
    assert {edition.status for edition in result.editions[1:]} == {"legacy"}


def test_new_tag_ages_out_oldest_without_deleting_retained_snapshot(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _release(repo, "1.0.0")
    _release(repo, "1.1.0")
    config = GitSourceConfig(repo=str(repo), ref="main", path="docs")
    app_root = tmp_path / "app"
    policy = _policy(count=2)
    first = sync_git_source(
        config,
        mount_id="docs",
        app_root=app_root,
        edition_policy=policy,
    )
    assert [edition.id for edition in first.editions] == ["latest", "1.1.0", "1.0.0"]

    _release(repo, "1.2.0")
    second = sync_git_source(
        config,
        mount_id="docs",
        app_root=app_root,
        edition_policy=policy,
        previous_editions=first.editions,
    )

    assert [edition.id for edition in second.editions] == [
        "latest",
        "1.2.0",
        "1.1.0",
        "1.0.0",
    ]
    assert second.editions[-1].status == "eol"
    assert second.editions[-1].resolved_ref == first.editions[-1].resolved_ref
    retained = second.content_root.parent.parent / "editions" / ".retained" / "1.0.0"
    assert retained.joinpath("docs", "guide.md").read_text() == "# 1.0.0\n"


def test_lifecycle_override_retains_release_beyond_count(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _release(repo, "1.0.0")
    _release(repo, "2.0.0")
    result = sync_git_source(
        GitSourceConfig(repo=str(repo), ref="main", path="docs"),
        mount_id="docs",
        app_root=tmp_path / "app",
        edition_policy=_policy(
            count=1,
            overrides={"1.0.0": {"status": "deprecated", "end_of_life": "2027-01-31"}},
        ),
    )

    assert [edition.id for edition in result.editions] == ["latest", "2.0.0", "1.0.0"]
    assert result.editions[-1].status == "deprecated"


def test_prerelease_filter_and_inclusion_match_bengal_order(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _release(repo, "1.0.0")
    _release(repo, "1.1.0-rc.1")
    config = GitSourceConfig(repo=str(repo), ref="main", path="docs")

    stable = sync_git_source(
        config,
        mount_id="stable",
        app_root=tmp_path / "app",
        edition_policy=_policy(count=1),
    )
    preview = sync_git_source(
        config,
        mount_id="preview",
        app_root=tmp_path / "app",
        edition_policy=_policy(count=1, include_prereleases=True),
    )

    assert [edition.id for edition in stable.editions] == ["latest", "1.0.0"]
    assert [edition.id for edition in preview.editions] == ["latest", "1.1.0-rc.1"]
    assert preview.editions[-1].status == "preview"


def test_normalization_collision_fails_before_replacing_last_known_good(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _release(repo, "1.0.0")
    config = GitSourceConfig(repo=str(repo), ref="main", path="docs")
    app_root = tmp_path / "app"
    first = sync_git_source(
        config,
        mount_id="docs",
        app_root=app_root,
        edition_policy=_policy(count=1),
    )
    original = first.editions[-1].content_root.joinpath("guide.md").read_text()
    _git(repo, "tag", "1.0.0")

    with pytest.raises(SourceSyncError, match="normalization collision"):
        sync_git_source(
            config,
            mount_id="docs",
            app_root=app_root,
            edition_policy=_policy(count=1, pattern="*"),
        )

    assert first.editions[-1].content_root.joinpath("guide.md").read_text() == original


def test_promotion_failure_restores_latest_and_edition_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    _release(repo, "1.0.0")
    config = GitSourceConfig(repo=str(repo), ref="main", path="docs")
    app_root = tmp_path / "app"
    first = sync_git_source(
        config,
        mount_id="docs",
        app_root=app_root,
        edition_policy=_policy(count=1),
    )
    latest_before = first.content_root.joinpath("guide.md").read_text()
    release_before = first.editions[-1].content_root.joinpath("guide.md").read_text()
    _release(repo, "2.0.0")
    real_replace = git_source.os.replace

    def fail_edition_backup(source: Path | str, target: Path | str) -> None:
        if Path(source).name == "editions" and Path(target).name.startswith(".editions-backup-"):
            raise OSError("injected edition backup failure")
        real_replace(source, target)

    monkeypatch.setattr(git_source.os, "replace", fail_edition_backup)
    with pytest.raises(OSError, match="injected edition backup failure"):
        sync_git_source(
            config,
            mount_id="docs",
            app_root=app_root,
            edition_policy=_policy(count=1),
        )

    assert first.content_root.joinpath("guide.md").read_text() == latest_before
    assert first.editions[-1].content_root.joinpath("guide.md").read_text() == release_before


@pytest.mark.parametrize(
    ("editions", "message"),
    [
        ({"count": -1}, "count must be a non-negative integer"),
        ({"sort": "newest"}, "sort 'newest' is unsupported"),
        ({"pattern": "v["}, "pattern 'v[' is malformed"),
        ({"include_prereleases": "yes"}, "include_prereleases must be true or false"),
        ({"aliases": []}, "aliases must be a mapping"),
        ({"aliases": {"stable": "bad/id"}}, "must be 'latest' or a URL-safe edition id"),
    ],
)
def test_mount_policy_validation_is_actionable(
    tmp_path: Path,
    editions: dict[str, object],
    message: str,
) -> None:
    mounts = tmp_path / "mounts.yaml"
    mounts.write_text(
        "mounts:\n"
        "  - id: docs\n"
        "    source:\n"
        "      provider: git\n"
        "      repo: https://example.test/docs.git\n"
        f"    editions: {editions!r}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message.replace("[", r"\[")):
        load_mounts(mounts, repo_root=tmp_path)


def test_editions_require_git_mount(tmp_path: Path) -> None:
    mounts = tmp_path / "mounts.yaml"
    mounts.write_text(
        "mounts:\n  - id: docs\n    content_root: docs\n    editions:\n      count: 3\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"require source\.provider: git"):
        load_mounts(mounts, repo_root=tmp_path)


def test_source_state_persists_normalized_policy_and_editions(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _release(repo, "1.0.0")
    policy = _policy(count=1)
    result = sync_git_source(
        GitSourceConfig(repo=str(repo), ref="main", path="docs"),
        mount_id="docs",
        app_root=tmp_path / "app",
        edition_policy=policy,
    )
    store = SourceSyncStateStore(tmp_path / "state")
    store.begin("docs", provider="git", source_repo=str(repo), requested_ref="main")
    state = store.reconcile(
        "docs",
        provider="git",
        resolved_ref=result.resolved_ref,
        content_root=result.content_root,
        source_url=result.source_url,
        editions=result.editions,
        edition_policy=policy,
    )

    last_known_good = state["last_known_good"]
    assert last_known_good["edition_policy"]["sort"] == "semver-desc"
    assert [edition["id"] for edition in last_known_good["editions"]] == [
        "latest",
        "1.0.0",
    ]


def test_registry_projects_discovered_editions_to_health_and_channels(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _release(repo, "1.0.0")
    app_root = tmp_path / "app"
    app_root.mkdir()
    mounts = app_root / "mounts.yaml"
    mounts.write_text(
        "mounts:\n"
        "  - id: docs\n"
        "    default: true\n"
        "    source:\n"
        "      provider: git\n"
        f"      repo: {repo}\n"
        "      ref: main\n"
        "      path: docs\n"
        "    editions:\n"
        "      source: tags\n"
        "      count: 1\n",
        encoding="utf-8",
    )
    catalog = CatalogRegistry.from_config(
        mounts,
        repo_root=tmp_path,
        app_root=app_root,
        autodoc=False,
    )

    health_editions = catalog.source_health()["mounts"][0]["editions"]
    public_editions = channel_manifest(catalog)["sources"][0]["editions"]
    assert [edition["id"] for edition in health_editions] == ["latest", "1.0.0"]
    assert [edition["id"] for edition in public_editions] == ["latest", "1.0.0"]
    assert "content_root" not in public_editions[0]
