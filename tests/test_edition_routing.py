"""Edition-aware live routing, agent discovery, and static export contracts."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from chirp.testing.client import TestClient
from pypdf import PdfReader

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.mcp import FuraMCPServer, MCPError
from furatena.catalog.pdf_export import PDFExportOptions, export_pdfs
from furatena.catalog.route_manifest import route_manifest_entries
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.static_export import StaticExportOptions, export_static_site
from furatena.catalog.versions import resolve_channel_target
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


def _docs(
    tmp_path: Path,
    *,
    historical_status: str = "legacy",
    historical_guide_visibility: str = "public",
) -> DocsApp:
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-b", "main")
    _git(remote, "config", "user.email", "tests@example.com")
    _git(remote, "config", "user.name", "Tests")
    content = remote / "docs"
    content.mkdir()
    topic = content / "topic"
    topic.mkdir()
    (topic / "_index.md").write_text("---\ntitle: Topic\n---\n# Topic\n\nTopic landing.\n")
    guide = content / "guide.md"
    guide.write_text(
        "---\n"
        "title: Old guide\n"
        f"visibility: {historical_guide_visibility}\n"
        "---\n"
        "# Old guide\n\nOld release.\n"
    )
    _git(remote, "add", "docs")
    _git(remote, "-c", "commit.gpgsign=false", "commit", "-m", "old")
    _git(remote, "tag", "v1.0.0")
    guide.write_text("---\ntitle: Latest guide\n---\n# Latest guide\n\nLatest release.\n")
    (topic / "new.md").write_text("---\ntitle: New topic\n---\n# New topic\n\nLatest only.\n")
    (content / "brand-new.md").write_text(
        "---\ntitle: Brand new\n---\n# Brand new\n\nLatest only.\n"
    )
    _git(remote, "add", "docs")
    _git(remote, "-c", "commit.gpgsign=false", "commit", "-m", "latest")

    app_root = tmp_path / "app"
    app_root.mkdir()
    copy_app_theme(app_root, APP_ROOT)
    write_minimal_docs_yaml(app_root / "docs.yaml")
    stable_target = "1.0.0" if historical_status == "legacy" else "latest"
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
        f"        stable: {stable_target}\n"
        "      overrides:\n"
        "        1.0.0:\n"
        f"          status: {historical_status}\n",
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
        assert 'data-edition-status="legacy"' in historical.text
        assert 'href="/guide/"' in historical.text
        historical_without_switcher = re.sub(
            r'<div class="version-selector">.*?</div>',
            "",
            historical.text,
            flags=re.DOTALL,
        )
        historical_without_switcher = re.sub(
            r'<div class="version-banner.*?</div>\s*</div>',
            "",
            historical_without_switcher,
            flags=re.DOTALL,
        )
        assert "/guide/" not in historical_without_switcher.replace("/v1.0.0/guide/", "")
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
    assert 'data-edition-status="legacy"' in historical.read_text(encoding="utf-8")
    assert 'href="/guide/"' in historical.read_text(encoding="utf-8")
    assert (output / "v1.0.0" / "guide.md").is_file()
    assert (output / "v1.0.0" / "llms.txt").is_file()
    assert (output / "v1.0.0" / "sitemap.xml").is_file()

    based_output = tmp_path / "based-public"
    export_static_site(
        docs,
        StaticExportOptions(output_dir=based_output, base_path="/project"),
    )
    latest = (based_output / "guide" / "index.html").read_text(encoding="utf-8")
    based_historical = (based_output / "v1.0.0" / "guide" / "index.html").read_text(
        encoding="utf-8"
    )
    assert 'href="/project/v1.0.0/guide/"' in latest
    assert 'href="/project/guide/"' in based_historical
    assert "window.location.assign(target.href)" in latest


def test_switcher_resolves_same_slug_fallbacks_and_htmx_targets(tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    client = TestClient(docs.create_app())

    async def exercise() -> None:
        async with client:
            direct = await client.get("/guide/")
            ancestor = await client.get("/topic/new/")

        assert 'value="/v1.0.0/guide/"' in direct.text
        assert 'data-resolution="direct"' in direct.text
        assert 'data-docs-version-target="1.0.0"' in direct.text
        assert 'hx-boost="true"' in direct.text
        assert 'value="/v1.0.0/topic/"' in ancestor.text
        assert 'data-resolution="ancestor"' in ancestor.text
        assert "target.click()" in direct.text
        assert "window.location.href = href" not in direct.text

    asyncio.run(exercise())


def test_switcher_omits_eol_sibling_edition(tmp_path: Path) -> None:
    docs = _docs(tmp_path, historical_status="eol")
    client = TestClient(docs.create_app())

    async def exercise() -> None:
        async with client:
            response = await client.get("/guide/")
        assert response.status == 200
        assert 'data-docs-version-target="1.0.0"' not in response.text
        assert 'class="version-selector"' not in response.text

    asyncio.run(exercise())


def test_eol_query_and_mcp_require_explicit_lifecycle_selection_without_bypassing_access(
    tmp_path: Path,
) -> None:
    docs = _docs(
        tmp_path,
        historical_status="eol",
        historical_guide_visibility="private",
    )
    client = TestClient(docs.create_app())

    async def exercise() -> tuple[dict, dict, dict, int]:
        async with client:
            default = await client.get("/v1.0.0/catalog/query.json")
            exact = await client.get("/v1.0.0/catalog/query.json?status=eol")
            broad = await client.get("/v1.0.0/catalog/query.json?include_eol=true")
            invalid = await client.get("/v1.0.0/catalog/query.json?status=unknown")
        return (
            json.loads(default.text),
            json.loads(exact.text),
            json.loads(broad.text),
            invalid.status,
        )

    default, exact, broad, invalid_status = asyncio.run(exercise())
    assert default["pages"] == []
    assert exact["pages"]
    assert broad["pages"]
    assert {page["edition_status"] for page in exact["pages"]} == {"eol"}
    assert all(page["slug"] != "guide" for page in exact["pages"])
    assert invalid_status == 400

    server = FuraMCPServer(docs)
    graph = server.call_tool(
        "query_graph",
        {"edition": "1.0.0", "status": "eol"},
    )["structuredContent"]
    assert graph["pages"]
    assert {page["edition_status"] for page in graph["pages"]} == {"eol"}
    with docs.catalog.use_edition("1.0.0"):
        topic = docs.catalog.get_by_slug("topic", mount="docs")
        assert topic is not None
        topic_id = topic.node_id
    with pytest.raises(MCPError, match="unknown node_id"):
        server.call_tool("retrieve_node", {"node_id": topic_id})
    allowed = server.call_tool("retrieve_node", {"node_id": topic_id, "include_eol": True})
    assert allowed["isError"] is False
    assert allowed["structuredContent"]["edition_status"] == "eol"

    historical = server.call_tool(
        "semantic_search",
        {
            "query": "Topic landing",
            "edition": "1.0.0",
            "status": "eol",
        },
    )["structuredContent"]
    private_probe = server.call_tool(
        "semantic_search",
        {
            "query": "Old release",
            "edition": "1.0.0",
            "status": "eol",
        },
    )["structuredContent"]
    assert historical["results"]
    assert {item["edition"] for item in historical["results"]} == {"1.0.0"}
    assert all(item["node_id"] != "docs:1.0.0:guide" for item in private_probe["results"])


def test_preview_exact_status_and_include_flag_are_both_opt_in_forms(tmp_path: Path) -> None:
    docs = _docs(tmp_path, historical_status="preview")
    client = TestClient(docs.create_app())

    async def exercise() -> tuple[dict, dict, dict]:
        async with client:
            default = await client.get("/v1.0.0/catalog/query.json")
            exact = await client.get("/v1.0.0/catalog/query.json?status=preview")
            broad = await client.get("/v1.0.0/catalog/query.json?include_preview=true")
        return json.loads(default.text), json.loads(exact.text), json.loads(broad.text)

    default, exact, broad = asyncio.run(exercise())
    assert default["pages"] == []
    assert exact["pages"] and broad["pages"]
    assert {page["edition_status"] for page in exact["pages"]} == {"preview"}


def test_pdf_uses_shared_lifecycle_banner_and_current_target(tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    with docs.catalog.use_edition("1.0.0"):
        result = export_pdfs(
            docs.catalog,
            options=PDFExportOptions(
                output_dir=tmp_path / "pdf",
                page="guide",
                base_url="https://docs.example.com",
                update_channel_manifest=False,
            ),
        )
    text = "\n".join(page.extract_text() or "" for page in PdfReader(result.paths[0]).pages)
    assert "This page documents a legacy edition." in text
    assert "View the current documentation" in text


def test_switcher_does_not_expose_inaccessible_sibling_page(tmp_path: Path) -> None:
    docs = _docs(tmp_path, historical_guide_visibility="private")
    client = TestClient(docs.create_app())

    async def exercise() -> None:
        async with client:
            response = await client.get("/guide/")
        assert response.status == 200
        assert 'data-docs-version-target="1.0.0"' not in response.text
        assert "/v1.0.0/guide/" not in response.text

    asyncio.run(exercise())

    output = tmp_path / "public"
    export_static_site(docs, StaticExportOptions(output_dir=output, base_path="/project"))
    latest = (output / "guide" / "index.html").read_text(encoding="utf-8")
    assert 'data-docs-version-target="1.0.0"' not in latest
    assert "/v1.0.0/guide/" not in latest


def test_switcher_falls_back_to_target_mount_landing() -> None:
    source = SimpleNamespace(mount="docs", slug="topic/new", url="/topic/new/")
    landing = SimpleNamespace(mount="docs", slug="", url="/v1.0.0/docs/")

    class Catalog:
        active_channel = "latest"
        mounts = (SimpleNamespace(id="docs", url_prefix="/docs/"),)

        @contextmanager
        def use_edition(self, edition: str):
            assert edition == "1.0.0"
            yield

        def discovered_editions_for(self, mount_id: str):
            assert mount_id == "docs"
            return (SimpleNamespace(id="1.0.0", status="legacy"),)

        def get_by_slug(self, slug: str, *, mount: str):
            assert mount == "docs"
            return None

        def get_path(self, path: str):
            return landing if path == "/v1.0.0/docs/" else None

        def can_access_node(self, node) -> bool:
            return node is landing

        def scoped_url(self, path: str) -> str:
            return path

    target = resolve_channel_target(Catalog(), source, "1.0.0")

    assert target is not None
    assert target.href == "/v1.0.0/docs/"
    assert target.resolution == "landing"
    assert target.resolved_slug == ""
