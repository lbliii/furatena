"""Static export (link phase) tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"
FROZEN_DIR = APP_ROOT / "frozen"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.registry import CatalogRegistry, MountConfig
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.static_export import (
    StaticExportOptions,
    _robots_txt,
    export_static_site,
    normalize_base_path,
    prefix_markdown_links,
    prefix_root_paths,
    url_path_to_output_file,
)
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml


class TestStaticExportHelpers:
    def test_url_path_to_output_file(self) -> None:
        assert url_path_to_output_file("/") == Path("index.html")
        assert url_path_to_output_file("/docs/foo/") == Path("docs/foo/index.html")
        assert url_path_to_output_file("/search.json") == Path("search.json")
        assert url_path_to_output_file("/docs/foo/index.txt") == Path("docs/foo/index.txt")

    def test_normalize_base_path(self) -> None:
        assert normalize_base_path("") == ""
        assert normalize_base_path("/") == ""
        assert normalize_base_path("/chirp") == "/chirp"
        assert normalize_base_path("chirp/") == "/chirp"

    def test_prefix_root_paths(self) -> None:
        html = '<a href="/docs/">Home</a><link rel="stylesheet" href="/static/app.css">'
        out = prefix_root_paths(html, "/chirp")
        assert 'href="/chirp/docs/"' in out
        assert 'href="/chirp/static/app.css"' in out


    def test_robots_txt(self) -> None:
        body = _robots_txt(site_url="https://example.github.io/chirp", base_path="/chirp")
        assert "Sitemap: https://example.github.io/chirp/sitemap.xml" in body

    def test_prefix_markdown_links(self) -> None:
        text = "- [Quickstart](/docs/get-started/quickstart/)\n"
        out = prefix_markdown_links(text, "/chirp")
        assert "(/chirp/docs/get-started/quickstart/)" in out


class TestMiniStaticExport:
    def test_export_writes_html_and_sidecars(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs_dir = content / "docs"
        docs_dir.mkdir(parents=True)
        (docs_dir / "hello.md").write_text(
            "---\ntitle: Hello\n---\n# Hello\n\nBody.\n",
            encoding="utf-8",
        )
        (content / "_index.md").write_text(
            "---\ntitle: Home\n---\n# Home\n",
            encoding="utf-8",
        )

        registry = CatalogRegistry(
            (
                MountConfig(
                    id="chirp",
                    label="Chirp",
                    content_root=content,
                    default=True,
                ),
            ),
            repo_root=tmp_path,
            autodoc=False,
        )
        frozen = tmp_path / "frozen"
        mount_dir = frozen / "mounts" / "chirp"
        pages = mount_dir / "pages"
        pages.mkdir(parents=True)
        shard_graph = {
            "schema_version": 2,
            "version": 2,
            "channel": "latest",
            "edition": "latest",
            "page_count": 0,
            "pages": [],
            "edges": [],
            "namespaces": [],
            "mount": "chirp",
        }
        merged_graph = {
            "schema_version": 2,
            "version": 2,
            "channel": "latest",
            "edition": "latest",
            "page_count": 0,
            "pages": [],
            "edges": [],
            "namespaces": [],
        }
        for node in registry.nodes:
            slug_path = node.slug or "index"
            target = pages / f"{slug_path}.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(node.body_html + "\n", encoding="utf-8")
            record = {
                "node_id": node.node_id,
                "url": node.url,
                "slug": node.slug,
                "title": node.title,
                "description": node.description,
                "section": node.section,
                "weight": node.weight,
                "tags": [],
                "source_path": node.source_path,
                "source": "markdown",
                "doc_version": None,
                "mount": node.mount,
                "edition": node.edition,
                "section_root": node.section_root,
                "toc": [],
                "backlinks": [],
            }
            shard_graph["pages"].append(record)
            merged_graph["pages"].append(record)
        shard_graph["page_count"] = len(shard_graph["pages"])
        merged_graph["page_count"] = len(merged_graph["pages"])
        (mount_dir / "catalog.json").write_text(json.dumps(shard_graph), encoding="utf-8")
        (frozen / "catalog.json").write_text(json.dumps(merged_graph), encoding="utf-8")
        (frozen / "registry.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "mounts": [{"id": "chirp", "label": "Chirp", "url_prefix": "/", "default": True}],
                }
            ),
            encoding="utf-8",
        )

        app_root = tmp_path / "app"
        app_root.mkdir()
        copy_app_theme(app_root, APP_ROOT)
        write_minimal_docs_yaml(app_root / "docs.yaml")
        write_mounts_yaml(app_root / "mounts.yaml", content)

        from furatena.catalog.config import load_docs_config

        config = load_docs_config(app_root / "docs.yaml")
        docs = DocsApp(
            config,
            repo_root=tmp_path,
            autodoc=False,
            serve=ServeConfig(ServeMode.PREVIEW, frozen, True, False),
        )
        out = tmp_path / "public"
        result = export_static_site(
            docs,
            StaticExportOptions(
                output_dir=out,
                base_path="",
                site_url="http://127.0.0.1:8080",
                include_index_txt=True,
                include_portal=False,
                include_search=False,
            ),
        )
        assert result.page_count >= 2
        assert (out / "index.html").is_file()
        assert (out / "docs/hello/index.html").is_file()
        assert (out / "catalog.json").is_file()
        assert (out / "channels.json").is_file()
        assert (out / "deployment-profiles.json").is_file()
        assert (out / "llms.txt").is_file()
        assert (out / "robots.txt").is_file()
        assert (out / ".nojekyll").is_file()
        profiles = json.loads((out / "deployment-profiles.json").read_text(encoding="utf-8"))
        profile_ids = {item["id"] for item in profiles["profiles"]}
        assert "static-pages" in profile_ids
        assert profiles["links"]["self"] == "http://127.0.0.1:8080/deployment-profiles.json"
        channels = json.loads((out / "channels.json").read_text(encoding="utf-8"))
        channel_ids = {item["id"] for item in channels["channels"]}
        assert {"static", "agent", "pdf"} <= channel_ids
        assert channels["mode"] == "static"
        assert channels["base_url"] == "http://127.0.0.1:8080"
        assert "catalog.json" in channels["channels"][1]["artifacts"]
        home = (out / "index.html").read_text(encoding="utf-8")
        assert "Home" in home
        assert 'id="page-root"' in home


@pytest.mark.slow
class TestFullStaticExportSmoke:
    def test_export_frozen_catalog(self, tmp_path: Path) -> None:
        if not (FROZEN_DIR / "catalog.json").is_file():
            pytest.skip("no frozen catalog")
        docs = DocsApp.from_paths(
            APP_ROOT / "docs.yaml",
            repo_root=REPO,
            autodoc=False,
            serve=ServeConfig(ServeMode.PREVIEW, FROZEN_DIR, True, False),
        )
        out = tmp_path / "public"
        result = export_static_site(
            docs,
            StaticExportOptions(
                output_dir=out,
                base_path="/furatena",
                site_url="https://example.github.io/furatena",
                include_index_txt=False,
                include_portal=True,
                include_search=True,
            ),
        )
        assert result.page_count >= max(160, len(docs.catalog.nodes))
        assert (out / "index.html").is_file()
        assert (out / "api" / "index.html").is_file()
        assert (out / "search/index.html").is_file()
        assert (out / "sitemap.xml").is_file()
        html = (out / "index.html").read_text(encoding="utf-8")
        assert 'href="/furatena/docs/' in html
        assert "https://example.github.io/furatena/" in html


class TestBodyMdFreeze:
    def test_frozen_catalog_restores_body_md(self) -> None:
        if not (FROZEN_DIR / "catalog.json").is_file():
            pytest.skip("no frozen catalog")
        import json

        from furatena.catalog.loader import DocCatalog

        graph = json.loads((FROZEN_DIR / "catalog.json").read_text(encoding="utf-8"))
        sample = next((page for page in graph.get("pages", []) if page.get("body_md")), None)
        if sample is None:
            pytest.skip("frozen catalog missing body_md — run fura freeze")
        mount = sample.get("mount") or "chirp"
        shard_dir = FROZEN_DIR / "mounts" / mount
        if not shard_dir.is_dir():
            pytest.skip("missing mount shard")
        catalog = DocCatalog.from_frozen(shard_dir, mount=mount)
        node = catalog.get_by_slug(sample["slug"])
        assert node is not None
        assert node.body_md.strip()


class TestIncrementalExport:
    def test_incremental_skips_unchanged_pages(self, tmp_path: Path) -> None:
        if not (FROZEN_DIR / "catalog.json").is_file():
            pytest.skip("no frozen catalog")
        from furatena.catalog.docs_app import DocsApp

        out = tmp_path / "public"
        docs = DocsApp.from_paths(
            APP_ROOT / "docs.yaml",
            repo_root=REPO,
            autodoc=False,
            serve=ServeConfig(ServeMode.PREVIEW, FROZEN_DIR, True, False),
        )
        first = export_static_site(
            docs,
            StaticExportOptions(
                output_dir=out,
                base_path="/chirp",
                frozen_dir=FROZEN_DIR,
                include_index_txt=False,
            ),
        )
        second = export_static_site(
            docs,
            StaticExportOptions(
                output_dir=out,
                base_path="/chirp",
                frozen_dir=FROZEN_DIR,
                include_index_txt=False,
                incremental=True,
            ),
        )
        assert first.page_count > 0
        assert second.page_count == 0
        assert second.skipped_count >= first.page_count
