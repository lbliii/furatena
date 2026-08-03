"""Edition-aware live routing, agent discovery, and static export contracts."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from chirp.testing.client import TestClient

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.route_manifest import route_manifest_entries
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.static_export import StaticExportOptions, export_static_site
from tests.support import copy_app_theme, write_minimal_docs_yaml

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _docs(tmp_path: Path) -> DocsApp:
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-b", "main")
    _git(remote, "config", "user.email", "tests@example.com")
    _git(remote, "config", "user.name", "Tests")
    content = remote / "docs"
    content.mkdir()
    guide = content / "guide.md"
    guide.write_text("---\ntitle: Old guide\n---\n# Old guide\n\nOld release.\n")
    _git(remote, "add", "docs/guide.md")
    _git(remote, "-c", "commit.gpgsign=false", "commit", "-m", "old")
    _git(remote, "tag", "v1.0.0")
    guide.write_text("---\ntitle: Latest guide\n---\n# Latest guide\n\nLatest release.\n")
    _git(remote, "add", "docs/guide.md")
    _git(remote, "-c", "commit.gpgsign=false", "commit", "-m", "latest")

    app_root = tmp_path / "app"
    app_root.mkdir()
    copy_app_theme(app_root, APP_ROOT)
    write_minimal_docs_yaml(app_root / "docs.yaml")
    (app_root / "mounts.yaml").write_text(
        "mounts:\n"
        "  - id: docs\n"
        "    label: Docs\n"
        "    default: true\n"
        "    source:\n"
        "      provider: git\n"
        f"      repo: {remote}\n"
        "      ref: main\n"
        "      path: docs\n"
        "    editions:\n"
        "      source: tags\n"
        "      count: 1\n"
        "      aliases:\n"
        "        latest: latest\n"
        "        stable: 1.0.0\n",
        encoding="utf-8",
    )
    return DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )


def test_live_routes_scope_pages_metadata_nav_and_agent_surfaces(tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    client = TestClient(docs.create_app())

    async def exercise() -> None:
        async with client:
            latest = await client.get("/guide/")
            historical = await client.get("/v1.0.0/guide/")
            alias = await client.get("/stable/guide/")
            latest_alias = await client.get("/latest/guide/")
            sitemap = await client.get("/v1.0.0/sitemap.xml")
            llms = await client.get("/v1.0.0/llms.txt")
            query = await client.get("/catalog/query.json?edition=1.0.0")
            semantic = await client.get("/search/semantic?q=old&edition=1.0.0")
            mismatch = await client.get("/v1.0.0/catalog/query.json?edition=latest")

        assert latest.status == 200 and "Latest guide" in latest.text
        assert historical.status == 200 and "Old guide" in historical.text
        assert "/v1.0.0/guide/" in historical.text
        assert "/guide/" not in historical.text.replace("/v1.0.0/guide/", "")
        assert alias.status == 301
        assert dict(alias.headers)["location"] == "/v1.0.0/guide/"
        assert latest_alias.status == 301
        assert dict(latest_alias.headers)["location"] == "/guide/"
        assert "/v1.0.0/guide/</loc>" in sitemap.text
        assert "[Old guide](/v1.0.0/guide.md)" in llms.text
        query_payload = json.loads(query.text)
        assert query_payload["edition"] == "1.0.0"
        assert {page["edition"] for page in query_payload["pages"]} == {"1.0.0"}
        semantic_payload = json.loads(semantic.text)
        assert semantic_payload["results"][0]["edition"] == "1.0.0"
        assert semantic_payload["results"][0]["url"].endswith("/v1.0.0/guide/")
        assert mismatch.status == 400
        assert json.loads(mismatch.text)["error"] == "edition context mismatch"

    asyncio.run(exercise())

    entries = route_manifest_entries(docs.create_app(), catalog=docs.catalog)
    paths = {entry.path for entry in entries}
    assert "/v1.0.0/{slug:path}" in paths
    assert "/v1.0.0/llms.txt" in paths
    assert "/stable/{slug:path}" in paths


def test_static_export_mirrors_historical_edition_layout(tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    output = tmp_path / "public"

    export_static_site(docs, StaticExportOptions(output_dir=output))

    historical = output / "v1.0.0" / "guide" / "index.html"
    assert historical.is_file()
    assert "Old guide" in historical.read_text(encoding="utf-8")
    assert (output / "v1.0.0" / "guide.md").is_file()
    assert (output / "v1.0.0" / "llms.txt").is_file()
    assert (output / "v1.0.0" / "sitemap.xml").is_file()
