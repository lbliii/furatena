"""Freeze-once immutable edition shard contracts."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from furatena.catalog import edition_shards
from furatena.catalog.edition_shards import freeze_edition_shard, freeze_edition_shards
from furatena.catalog.exceptions import ExportError
from furatena.catalog.freeze import FreezeCatalogOptions, freeze_catalog
from furatena.catalog.registry import CatalogRegistry


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path, versions: tuple[str, ...]) -> Path:
    repo = tmp_path / "remote"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Tests")
    docs = repo / "docs"
    docs.mkdir()
    for version in versions:
        (docs / "guide.md").write_text(
            f"---\ntitle: Guide {version}\n---\n# Guide {version}\n\nRelease {version}.\n",
            encoding="utf-8",
        )
        _git(repo, "add", "docs/guide.md")
        _git(repo, "-c", "commit.gpgsign=false", "commit", "-m", version)
        _git(repo, "tag", f"v{version}")
    return repo


def _mounts(app_root: Path, repo: Path, *, count: int) -> Path:
    mounts = app_root / "mounts.yaml"
    mounts.write_text(
        "mounts:\n"
        "  - id: docs\n"
        "    label: Docs\n"
        "    default: true\n"
        "    source:\n"
        "      provider: git\n"
        f"      repo: {repo}\n"
        "      ref: main\n"
        "      path: docs\n"
        "    editions:\n"
        "      source: tags\n"
        f"      count: {count}\n",
        encoding="utf-8",
    )
    return mounts


def _registry(tmp_path: Path, versions: tuple[str, ...]) -> tuple[CatalogRegistry, Path]:
    repo = _repo(tmp_path, versions)
    app_root = tmp_path / "app"
    app_root.mkdir()
    mounts = _mounts(app_root, repo, count=len(versions))
    registry = CatalogRegistry.from_config(
        mounts,
        repo_root=tmp_path,
        app_root=app_root,
        autodoc=False,
    )
    return registry, tmp_path / "frozen"


def test_release_shard_contains_semantic_ir_without_rendered_pages(tmp_path: Path) -> None:
    registry, frozen = _registry(tmp_path, ("1.0.0",))

    (status,) = freeze_edition_shards(registry, frozen)
    shard = frozen / "mounts" / "docs" / "1.0.0"
    manifest = json.loads((shard / "fingerprint.json").read_text())
    catalog = json.loads((shard / "catalog.json").read_text())

    assert status.status == "frozen"
    assert status.page_count == 1
    assert catalog["edition"] == "1.0.0"
    assert catalog["pages"][0]["node_id"].startswith("docs:1.0.0:")
    assert catalog["pages"][0]["source_ref"] == status.resolved_ref
    assert (shard / "content" / "guide.json").is_file()
    assert (shard / "ast" / "guide.json").is_file()
    assert not (shard / "pages").exists()
    assert not list(shard.rglob("*.html"))
    assert manifest["contracts"] == {"adapter": 1, "content_ir": 3, "dcp": 3}
    assert len(manifest["fingerprint"]) == 64
    assert manifest["artifacts"]


def test_many_unchanged_editions_reuse_without_constructing_catalogs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    versions = ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0")
    registry, frozen = _registry(tmp_path, versions)
    first = freeze_edition_shards(registry, frozen)
    mtimes = {status.edition: status.path.stat().st_mtime_ns for status in first}

    def fail_build(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unchanged historical edition was indexed")

    monkeypatch.setattr(registry, "_build_live_shard", fail_build)
    second = freeze_edition_shards(registry, frozen)

    assert len(second) == len(versions)
    assert {status.status for status in second} == {"reused"}
    assert {status.edition: status.path.stat().st_mtime_ns for status in second} == mtimes


def test_corruption_rebuilds_but_schema_change_deliberately_invalidates(
    tmp_path: Path,
) -> None:
    registry, frozen = _registry(tmp_path, ("1.0.0",))
    (first,) = freeze_edition_shards(registry, frozen)
    content = first.path / "content" / "guide.json"
    original = content.read_bytes()
    content.write_text("corrupt\n", encoding="utf-8")

    (repaired,) = freeze_edition_shards(registry, frozen)
    assert repaired.status == "frozen"
    assert content.read_bytes() == original
    assert repaired.fingerprint == first.fingerprint

    (schema_bump,) = freeze_edition_shards(
        registry,
        frozen,
        content_ir_schema_version=4,
    )
    assert schema_bump.status == "frozen"
    assert schema_bump.input_fingerprint != repaired.input_fingerprint
    assert schema_bump.fingerprint != repaired.fingerprint


def test_atomic_schema_rebuild_failure_preserves_verified_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, frozen = _registry(tmp_path, ("1.0.0",))
    (first,) = freeze_edition_shards(registry, frozen)
    before = (first.path / "fingerprint.json").read_bytes()

    def fail_build(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("injected edition build failure")

    monkeypatch.setattr(edition_shards, "_build_shard", fail_build)
    with pytest.raises(RuntimeError, match="injected edition build failure"):
        freeze_edition_shards(registry, frozen, content_ir_schema_version=4)

    assert (first.path / "fingerprint.json").read_bytes() == before
    assert not list(first.path.parent.glob(".1.0.0.edition-freeze.pending.*"))


def test_moved_tag_is_an_integrity_failure_not_an_overwrite(tmp_path: Path) -> None:
    registry, frozen = _registry(tmp_path, ("1.0.0",))
    mount = registry.mounts[0]
    snapshot = registry.discovered_editions_for("docs")[1]
    first = freeze_edition_shard(registry, mount, snapshot, frozen)
    moved = replace(snapshot, resolved_ref="0" * 40)

    with pytest.raises(ExportError, match="release tag moved"):
        freeze_edition_shard(registry, mount, moved, frozen)

    assert (
        json.loads((first.path / "fingerprint.json").read_text())["source"]["resolved_ref"]
        == snapshot.resolved_ref
    )


def test_renderer_change_rebuilds_latest_but_reuses_release_shard(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ("1.0.0",))
    app_root = tmp_path / "app"
    app_root.mkdir()
    _mounts(app_root, repo, count=1)
    docs_config = app_root / "docs.yaml"
    docs_config.write_text(
        "site:\n  name: Edition Test\ntheme:\n  id: furatena\nmounts: mounts.yaml\n",
        encoding="utf-8",
    )
    options = FreezeCatalogOptions(
        docs_config=docs_config,
        app_root=app_root,
        repo_root=tmp_path,
        output_dir=app_root / "frozen",
        autodoc=False,
    )

    first = freeze_catalog(options)
    fingerprint = app_root / "frozen" / "mounts" / "docs" / "1.0.0" / "fingerprint.json"
    before = fingerprint.read_bytes()
    before_mtime = fingerprint.stat().st_mtime_ns
    theme = app_root / "theme"
    theme.mkdir()
    (theme / "probe.css").write_text("body { color: rebeccapurple; }\n", encoding="utf-8")

    second = freeze_catalog(options)

    assert first.frozen_editions == ("docs:1.0.0",)
    assert second.frozen_mounts == ("docs",)
    assert second.frozen_editions == ()
    assert second.reused_editions == ("docs:1.0.0",)
    assert fingerprint.read_bytes() == before
    assert fingerprint.stat().st_mtime_ns == before_mtime
    manifest = json.loads((app_root / "frozen" / "freeze.manifest.json").read_text())
    assert manifest["edition_shards"][0]["status"] == "reused"
