"""Mike-compatible edition artifacts across live, frozen, and static delivery."""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from chirp.testing import TestClient

from furatena.catalog.access import AccessPolicy
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.freeze import FreezeCatalogOptions, freeze_catalog
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.sources.types import GitEditionPolicy, GitEditionSnapshot
from furatena.catalog.static_export import StaticExportOptions, export_static_site
from furatena.catalog.version_artifacts import versions_for_mount, versions_manifest
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml

REPO = Path(__file__).resolve().parents[1]


def _versioned_docs(tmp_path: Path) -> DocsApp:
    app_root = tmp_path / "app"
    content = app_root / "content"
    content.mkdir(parents=True)
    (content / "_index.md").write_text("---\ntitle: Home\n---\n# Home\n", encoding="utf-8")
    copy_app_theme(app_root, REPO / "app")
    write_minimal_docs_yaml(app_root / "docs.yaml")
    write_mounts_yaml(
        app_root / "mounts.yaml",
        content,
        mount_id="docs",
        url_prefix="/docs",
    )
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    policy = GitEditionPolicy(aliases={"latest": "latest", "stable": "0.8.2", "previous": "0.7.1"})
    docs.catalog.mounts = (replace(docs.catalog.mounts[0], editions=policy),)
    docs.catalog._discovered_editions["docs"] = (
        _snapshot("0.8.2", content),
        _snapshot("latest", content),
        _snapshot("0.7.1", content),
    )
    return docs


def _snapshot(edition: str, content: Path) -> GitEditionSnapshot:
    return GitEditionSnapshot(
        id=edition,
        ref="main" if edition == "latest" else f"v{edition}",
        resolved_ref=(edition.replace(".", "") or "0") * 8,
        content_root=content,
        status="current" if edition == "latest" else "legacy",
        prerelease=False,
        discovered_at="2026-08-03T00:00:00Z",
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    )


def _frozen_versioned_app(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.email", "tests@example.com")
    _git(source, "config", "user.name", "Tests")
    content = source / "docs"
    content.mkdir()
    for edition in ("0.7.1", "0.8.2"):
        (content / "_index.md").write_text(
            f"---\ntitle: Docs {edition}\n---\n# Docs {edition}\n",
            encoding="utf-8",
        )
        _git(source, "add", "docs/_index.md")
        _git(source, "-c", "commit.gpgsign=false", "commit", "-m", edition)
        _git(source, "tag", f"v{edition}")

    app_root = tmp_path / "app"
    app_root.mkdir()
    copy_app_theme(app_root, REPO / "app")
    write_minimal_docs_yaml(app_root / "docs.yaml")
    (app_root / "mounts.yaml").write_text(
        "mounts:\n"
        "  - id: docs\n"
        "    label: Docs\n"
        "    default: true\n"
        "    url_prefix: /docs\n"
        "    source:\n"
        "      provider: git\n"
        f"      repo: {source}\n"
        "      ref: main\n"
        "      path: docs\n"
        "    editions:\n"
        "      source: tags\n"
        "      count: 2\n"
        "      aliases:\n"
        "        latest: latest\n"
        "        stable: 0.8.2\n"
        "        previous: 0.7.1\n",
        encoding="utf-8",
    )
    frozen = app_root / "frozen"
    freeze_catalog(
        FreezeCatalogOptions(
            docs_config=app_root / "docs.yaml",
            app_root=app_root,
            repo_root=tmp_path,
            output_dir=frozen,
            autodoc=False,
        )
    )
    return app_root, frozen


def test_versions_payload_is_mike_compatible_mount_keyed_and_deterministic(
    tmp_path: Path,
) -> None:
    docs = _versioned_docs(tmp_path)

    per_mount = versions_for_mount(docs.catalog, "docs", base_path="/project")

    assert per_mount == [
        {
            "version": "latest",
            "title": "Latest",
            "aliases": ["latest"],
            "url_prefix": "/project/docs",
        },
        {
            "version": "0.8.2",
            "title": "0.8.2",
            "aliases": ["stable"],
            "url_prefix": "/project/v0.8.2/docs",
        },
        {
            "version": "0.7.1",
            "title": "0.7.1",
            "aliases": ["previous"],
            "url_prefix": "/project/v0.7.1/docs",
        },
    ]
    assert versions_manifest(docs.catalog, base_url="https://example.com/project") == {
        "schema_version": 1,
        "mounts": {"docs": per_mount},
    }
    assert all(set(item) == {"version", "title", "aliases", "url_prefix"} for item in per_mount)


def test_versions_exclude_nonpublic_mounts_from_hub_routes_and_channels(tmp_path: Path) -> None:
    docs = _versioned_docs(tmp_path)
    public = docs.catalog.mounts[0]
    private = replace(
        public,
        id="secret",
        label="Secret",
        default=False,
        access=AccessPolicy(visibility="private"),
    )
    docs.catalog.mounts = (private, public)
    docs.catalog._discovered_editions["secret"] = docs.catalog._discovered_editions["docs"]

    assert list(versions_manifest(docs.catalog)["mounts"]) == ["docs"]
    with pytest.raises(KeyError):
        versions_for_mount(docs.catalog, "secret")

    client = TestClient(docs.create_app())

    async def fetch() -> tuple[int, dict[str, Any]]:
        denied = await client.get("/versions/mounts/secret.json")
        channels = await client.get("/channels.json")
        return denied.status, json.loads(channels.text)

    status, channels = asyncio.run(fetch())
    outputs = next(item for item in channels["channels"] if item["id"] == "agent")["outputs"]
    assert status == 404
    assert not any(
        item.get("mount") == "secret" and item["id"].startswith("versions-") for item in outputs
    )


def test_versions_live_static_and_channel_discovery_are_identical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = _versioned_docs(tmp_path)
    monkeypatch.setenv("FURA_BASE_URL", "https://example.com/project")
    client = TestClient(docs.create_app())

    async def fetch_live() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        hub = await client.get("/versions.json")
        mount = await client.get("/versions/mounts/docs.json")
        channels = await client.get("/channels.json")
        assert hub.status == mount.status == channels.status == 200
        return json.loads(hub.text), json.loads(mount.text), json.loads(channels.text)

    live_hub, live_mount, live_channels = asyncio.run(fetch_live())
    agent = next(item for item in live_channels["channels"] if item["id"] == "agent")
    outputs = {item["id"]: item for item in agent["outputs"]}
    assert outputs["versions"]["url"] == "https://example.com/project/versions.json"
    assert outputs["versions-mount-docs"]["url"].endswith("/versions/mounts/docs.json")

    output = tmp_path / "public"
    monkeypatch.setattr(
        "furatena.catalog.static_export._export_editions",
        lambda _docs: ("latest",),
    )
    export_static_site(
        docs,
        StaticExportOptions(
            output_dir=output,
            base_path="/project",
            site_url="https://example.com/project",
            include_index_txt=False,
            include_portal=False,
            include_search=False,
        ),
    )

    assert json.loads((output / "versions.json").read_text(encoding="utf-8")) == live_hub
    assert (
        json.loads((output / "versions/mounts/docs.json").read_text(encoding="utf-8")) == live_mount
    )
    static_channels = json.loads((output / "channels.json").read_text(encoding="utf-8"))
    static_agent = next(item for item in static_channels["channels"] if item["id"] == "agent")
    static_outputs = {item["id"]: item for item in static_agent["outputs"]}
    assert static_outputs["versions"]["url"] == outputs["versions"]["url"]
    assert "versions.json" in static_channels["channels"][1]["artifacts"]
    assert "versions/mounts/docs.json" in static_channels["channels"][1]["artifacts"]


def test_frozen_preview_rehydrates_editions_for_routes_and_switcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FURA_BASE_URL", "https://example.com/project")
    app_root, frozen = _frozen_versioned_app(tmp_path)
    frozen_hub = json.loads((frozen / "versions.json").read_text(encoding="utf-8"))
    frozen_mount = json.loads((frozen / "versions/mounts/docs.json").read_text(encoding="utf-8"))
    assert "/sitemaps/docs.xml</loc>" in (frozen / "sitemap.xml").read_text(encoding="utf-8")
    assert "[Docs](/llms/docs.txt)" in (frozen / "llms.txt").read_text(encoding="utf-8")
    assert "/docs/</loc>" in (frozen / "sitemaps/docs.xml").read_text(encoding="utf-8")
    assert "[Docs 0.8.2](/docs.md)" in (frozen / "llms/docs.txt").read_text(encoding="utf-8")
    preview = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, frozen, True, False),
    )

    assert preview.catalog.edition_ids_for("docs") == ("latest", "0.8.2", "0.7.1")
    assert [channel.id for channel in preview.catalog.channels] == ["latest", "0.8.2", "0.7.1"]
    assert [item.status for item in preview.catalog.discovered_editions_for("docs")] == [
        "current",
        "legacy",
        "legacy",
    ]
    client = TestClient(preview.create_app())

    async def fetch_preview() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        hub = await client.get("/versions.json")
        mount = await client.get("/versions/mounts/docs.json")
        assert hub.status == mount.status == 200
        return json.loads(hub.text), json.loads(mount.text)

    preview_hub, preview_mount = asyncio.run(fetch_preview())
    assert preview_hub == frozen_hub
    assert preview_mount == frozen_mount
