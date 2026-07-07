"""Tests for format-agnostic documentation source ingestion."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.export import catalog_graph, meta_json
from furatena.catalog.freeze_incremental import mount_source_statuses
from furatena.catalog.loader import DocCatalog
from furatena.catalog.models import DocNode
from furatena.catalog.registry import CatalogRegistry, load_mounts
from furatena.catalog.runtime import ServeMode
from furatena.catalog.sources import (
    FilesystemScanner,
    FilesystemSourceProvider,
    GitSourceConfig,
    GitSourceProvider,
    MountSourceConfig,
    get_content_adapter,
    sync_git_source,
)
from furatena.catalog.sources.parse import parse_source_text
from furatena.catalog.sources.registry import registered_formats
from furatena.catalog.sources.scanner import file_to_url


class TestMountSourceConfig:
    def test_registered_formats(self) -> None:
        formats = registered_formats()
        assert "html" in formats
        assert "docutils-rst" in formats
        assert "mdx" in formats
        assert "myst-markdown" in formats

    def test_default_tracks_markdown(self) -> None:
        config = MountSourceConfig()
        assert ".md" in config.tracked_extensions()
        assert config.content_format_for(Path("docs/page.md")) == "patitas-markdown"

    def test_from_mount_dict(self) -> None:
        config = MountSourceConfig.from_mount_dict(
            {
                "extensions": [".md", ".html", ".rst", ".mdx", ".myst"],
            }
        )
        assert config.content_format_for(Path("page.html")) == "html"
        assert config.content_format_for(Path("page.rst")) == "docutils-rst"
        assert config.content_format_for(Path("page.mdx")) == "mdx"
        assert config.content_format_for(Path("page.myst")) == "myst-markdown"
        assert "index.html" in config.index_files
        assert "index.rst" in config.index_files
        assert "index.myst" in config.index_files

    def test_mount_can_declare_myst_markdown_for_md_files(self) -> None:
        config = MountSourceConfig.from_mount_dict(
            {
                "extensions": [".md"],
                "format_map": {".md": "myst-markdown"},
            }
        )
        assert config.content_format_for(Path("page.md")) == "myst-markdown"

    def test_from_mount_dict_parses_git_source(self) -> None:
        config = MountSourceConfig.from_mount_dict(
            {
                "source": {
                    "provider": "git",
                    "repo": "https://github.com/example/docs.git",
                    "ref": "main",
                    "path": "docs",
                },
                "extensions": [".md", ".mdx"],
            }
        )
        assert config.provider == "git"
        assert config.git is not None
        assert config.git.repo == "https://github.com/example/docs.git"
        assert config.git.ref == "main"
        assert config.git.path == "docs"
        assert ".mdx" in config.tracked_extensions()

    def test_absolute_content_root_is_canonicalized(self, tmp_path: Path) -> None:
        real_root = tmp_path / "real-docs"
        real_root.mkdir()
        alias_root = tmp_path / "docs-alias"
        alias_root.symlink_to(real_root, target_is_directory=True)
        mounts_yaml = tmp_path / "mounts.yaml"
        mounts_yaml.write_text(
            "\n".join(
                (
                    "mounts:",
                    "  - id: docs",
                    f"    content_root: {alias_root}",
                    "    default: true",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        (mount,) = load_mounts(mounts_yaml, repo_root=tmp_path)

        assert mount.content_root == real_root.resolve()


class TestFilesystemScanner:
    def test_scan_indexes_markdown(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        page = docs / "hello.md"
        page.write_text(
            "---\ntitle: Hello\n---\n\n# Hello\n\n[Link](/docs/world/)\n",
            encoding="utf-8",
        )
        scanner = FilesystemScanner(MountSourceConfig())
        pages = scanner.scan(tmp_path)
        assert len(pages) == 1
        assert pages[0].slug == "docs/hello"
        assert pages[0].content_format == "patitas-markdown"
        assert "Hello" in pages[0].body

    def test_index_file_maps_to_section_url(self, tmp_path: Path) -> None:
        section = tmp_path / "docs" / "guide"
        section.mkdir(parents=True)
        (section / "_index.md").write_text("---\ntitle: Guide\n---\n\nBody\n", encoding="utf-8")
        url, slug = file_to_url(tmp_path, section / "_index.md")
        assert slug == "docs/guide"
        assert url == "/docs/guide/"

    def test_invalid_utf8_fails_with_source_decode_error(self, tmp_path: Path) -> None:
        page = tmp_path / "docs" / "invalid.md"
        page.parent.mkdir()
        page.write_bytes(b"---\ntitle: Invalid\n---\n\xff\xfe")

        scanner = FilesystemScanner(MountSourceConfig())

        with pytest.raises(UnicodeDecodeError):
            scanner.scan(tmp_path)

    def test_malformed_frontmatter_is_skipped_by_scanner(self, tmp_path: Path) -> None:
        page = tmp_path / "docs" / "broken.md"
        page.parent.mkdir()
        page.write_text("---\ntitle: [broken\n---\n# Broken\n", encoding="utf-8")

        scanner = FilesystemScanner(MountSourceConfig())

        assert scanner.scan(tmp_path) == []

    def test_source_parser_rejects_malformed_frontmatter(self) -> None:
        with pytest.raises(Exception, match="flow sequence"):
            parse_source_text(
                "---\ntitle: [broken\n---\n# Broken\n",
                content_format="patitas-markdown",
            )


class TestFilesystemSourceProvider:
    def test_provider_enumerates_reads_fingerprints_and_reports_provenance(
        self,
        tmp_path: Path,
    ) -> None:
        page = tmp_path / "docs" / "hello.md"
        page.parent.mkdir()
        page.write_text("---\ntitle: Hello\n---\n\n# Hello\n", encoding="utf-8")
        provider = FilesystemSourceProvider(MountSourceConfig())

        sources = provider.enumerate(tmp_path)
        assert len(sources) == 1
        source = sources[0]
        assert provider.read(source).startswith("---")
        fingerprint = provider.fingerprint(source)
        assert fingerprint.algorithm == "sha256"
        assert len(fingerprint.value) == 64
        assert fingerprint.size == page.stat().st_size
        provenance = provider.provenance(source, mount="docs")
        assert provenance.provider == "filesystem"
        assert provenance.path == "docs/hello.md"
        assert provenance.mount == "docs"
        assert provenance.to_meta()["source_provider"] == "filesystem"


def _git(*args: str, cwd: Path) -> str:
    try:
        result = subprocess.run(
            ("git", *args),
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        pytest.skip("git executable is required")
    return result.stdout.strip()


def _make_git_docs_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "remote-docs"
    repo.mkdir()
    _git("init", cwd=repo)
    _git("config", "user.email", "tests@example.com", cwd=repo)
    _git("config", "user.name", "Tests", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    docs = repo / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(
        "---\ntitle: Git Guide\n---\n\n# Git Guide\n\nSynced body.\n",
        encoding="utf-8",
    )
    _git("add", "docs/guide.md", cwd=repo)
    _git("commit", "-m", "Add docs", cwd=repo)
    commit = _git("rev-parse", "HEAD", cwd=repo)
    return repo, commit


def _write_frozen_mount(root: Path, *, mount: str, slug: str, title: str, url: str) -> None:
    mount_dir = root / "mounts" / mount
    pages_dir = mount_dir / "pages"
    pages_dir.mkdir(parents=True)
    (mount_dir / "catalog.json").write_text(
        (
            "{\n"
            '  "schema_version": 3,\n'
            '  "channel": "latest",\n'
            f'  "mount": "{mount}",\n'
            '  "pages": [\n'
            "    {\n"
            f'      "url": "{url}",\n'
            f'      "slug": "{slug}",\n'
            f'      "title": "{title}",\n'
            '      "source_path": "guide.md",\n'
            '      "content_format": "patitas-markdown",\n'
            '      "body_source": "# Frozen Guide",\n'
            '      "body_text": "Frozen Guide",\n'
            '      "toc": []\n'
            "    }\n"
            "  ],\n"
            '  "edges": []\n'
            "}\n"
        ),
        encoding="utf-8",
    )
    (pages_dir / f"{slug}.html").write_text("<h1>Frozen Guide</h1>\n", encoding="utf-8")


class TestGitSourceProvider:
    @pytest.mark.parametrize("repo_kind", ("unreachable", "corrupt"))
    def test_git_sync_rejects_unreachable_and_corrupt_repositories(
        self,
        tmp_path: Path,
        repo_kind: str,
    ) -> None:
        repo = tmp_path / f"{repo_kind}-repo"
        if repo_kind == "corrupt":
            (repo / ".git").mkdir(parents=True)
        config = GitSourceConfig(repo=repo.as_posix(), ref="main")

        with pytest.raises(RuntimeError, match=r"git clone .* failed"):
            sync_git_source(config, mount_id="remote", app_root=tmp_path / "app")

    def test_registry_syncs_git_mount_and_exports_commit_provenance(self, tmp_path: Path) -> None:
        repo, commit = _make_git_docs_repo(tmp_path)
        app_root = tmp_path / "app"
        app_root.mkdir()
        mounts_yaml = app_root / "mounts.yaml"
        mounts_yaml.write_text(
            f"""
mounts:
  - id: remote
    label: Remote Docs
    url_prefix: /remote
    source:
      provider: git
      repo: {repo.as_posix()}
      ref: HEAD
      path: docs
    extensions: [".md"]
""".lstrip(),
            encoding="utf-8",
        )

        registry = CatalogRegistry.from_config(
            mounts_yaml, repo_root=tmp_path, app_root=app_root, autodoc=False
        )

        node = registry.get_by_slug("guide", mount="remote")
        assert node is not None
        assert node.title == "Git Guide"
        assert node.meta["source_provider"] == "git"
        assert node.meta["source_repo"] == repo.as_posix()
        assert node.meta["source_ref"] == commit
        assert node.meta["source_url"].startswith(repo.resolve().as_uri())
        assert (
            registry.mounts[0].content_root
            == app_root / ".docs-cache" / "sources" / "remote" / "repo" / "docs"
        )

        payload = catalog_graph(registry, schema_version=3)
        page = payload["pages"][0]
        assert page["source_provider"] == "git"
        assert page["source_repo"] == repo.as_posix()
        assert page["source_ref"] == commit
        assert page["source_url"].endswith("/docs/guide.md")
        assert page["provenance"]["source_url"] == page["source_url"]

        status = mount_source_statuses(
            registry,
            tmp_path / "frozen",
            renderer_fingerprint="renderer",
            renderer_changed=False,
        )["remote"]
        assert status["provider"] == "git"
        assert status["source_repo"] == repo.as_posix()
        assert status["source_ref"] == commit
        assert status["source_url"] == repo.resolve().as_uri()

        health = registry.source_health()["mounts"][0]
        assert health["status"] == "healthy"
        assert health["provider"] == "git"
        assert health["source"]["repo"] == repo.as_posix()
        assert health["source"]["ref"] == commit
        assert health["loaded"] is True

    def test_registry_scopes_git_cache_by_catalog_identity(self, tmp_path: Path) -> None:
        repo, _commit = _make_git_docs_repo(tmp_path)
        app_root = tmp_path / "app"
        app_root.mkdir()
        mounts_yaml = app_root / "mounts.yaml"
        mounts_yaml.write_text(
            f"""
mounts:
  - id: remote
    label: Remote Docs
    url_prefix: /remote
    source:
      provider: git
      repo: {repo.as_posix()}
      ref: HEAD
      path: docs
    extensions: [".md"]
""".lstrip(),
            encoding="utf-8",
        )

        registry = CatalogRegistry.from_config(
            mounts_yaml,
            repo_root=tmp_path,
            app_root=app_root,
            autodoc=False,
            catalog_identity={
                "tenant": "Acme Inc",
                "workspace": "Platform",
                "site": "Developer Docs",
            },
        )

        assert registry.mounts[0].content_root == (
            app_root
            / ".docs-cache"
            / "sources"
            / "tenants"
            / "acme-inc"
            / "workspaces"
            / "platform"
            / "sites"
            / "developer-docs"
            / "remote"
            / "repo"
            / "docs"
        )

    def test_hybrid_registry_serves_frozen_shard_when_git_sync_fails(self, tmp_path: Path) -> None:
        app_root = tmp_path / "app"
        app_root.mkdir()
        frozen = app_root / "frozen"
        _write_frozen_mount(
            frozen,
            mount="remote",
            slug="guide",
            title="Frozen Guide",
            url="/remote/guide/",
        )
        mounts_yaml = app_root / "mounts.yaml"
        mounts_yaml.write_text(
            f"""
mounts:
  - id: remote
    label: Remote Docs
    url_prefix: /remote
    source:
      provider: git
      repo: {(tmp_path / "missing-repo").as_posix()}
      ref: main
      path: docs
    extensions: [".md"]
""".lstrip(),
            encoding="utf-8",
        )

        registry = CatalogRegistry.from_config(
            mounts_yaml,
            repo_root=tmp_path,
            app_root=app_root,
            autodoc=False,
            frozen_dir=frozen,
            lazy_html=True,
            serve_mode=ServeMode.HYBRID,
        )

        node = registry.get("/remote/guide/")
        assert node is not None
        assert node.title == "Frozen Guide"
        assert registry.body_html(node) == "<h1>Frozen Guide</h1>"
        health = registry.source_health()["mounts"][0]
        assert health["status"] == "degraded"
        assert health["loaded"] is True
        assert health["loaded_from"].startswith("frozen")
        assert health["source"]["status"] == "failed"
        assert health["source"]["error"]["type"] == "RuntimeError"
        assert health["source"]["error"]["domain_type"] == "SourceSyncError"
        assert health["source"]["error"]["code"] == "fura.source_sync"
        assert health["source"]["error"]["context"]["mount"] == "remote"
        freeze_status = mount_source_statuses(
            registry,
            frozen,
            renderer_fingerprint="renderer",
            renderer_changed=False,
        )["remote"]
        assert freeze_status["health_status"] == "degraded"
        assert freeze_status["dirty"] is True
        assert "missing_source_fingerprint" in freeze_status["drift_reasons"]

    def test_git_provider_reports_blob_url_for_synced_source(self, tmp_path: Path) -> None:
        repo, commit = _make_git_docs_repo(tmp_path)
        config = MountSourceConfig.from_mount_dict(
            {
                "source": {
                    "provider": "git",
                    "repo": repo.as_posix(),
                    "ref": "HEAD",
                    "path": "docs",
                }
            }
        ).with_git_sync_state(
            resolved_ref=commit,
            source_url=repo.resolve().as_uri(),
        )
        provider = GitSourceProvider(config)
        source = provider.enumerate(repo / "docs")[0]

        provenance = provider.provenance(source, mount="remote")

        assert provenance.provider == "git"
        assert provenance.repo == repo.as_posix()
        assert provenance.ref == commit
        assert provenance.source_url == f"{repo.resolve().as_uri()}/docs/guide.md"


class TestPatitasMarkdownAdapter:
    def test_adapt_populates_content_ir_and_sections(self, tmp_path: Path) -> None:
        adapter = get_content_adapter("patitas-markdown")
        source_path = "docs/test.md"
        body = "# Title\n\nIntro.\n\n## Section\n\nDetails.\n"
        from furatena.catalog.context import NodeStub
        from furatena.catalog.sources.types import PageSource

        source = PageSource(
            path=tmp_path / source_path,
            content_format="patitas-markdown",
            meta={"title": "Title"},
            body=body,
            source_path=source_path,
            url="/docs/test/",
            slug="docs/test",
        )
        stubs = {
            "docs/test": NodeStub(
                slug="docs/test",
                url="/docs/test/",
                title="Title",
                description="",
                weight=100,
                page_type="doc",
            )
        }

        def render_markdown(*_args, **_kwargs) -> str:
            return ""

        adapted = adapter.adapt(
            source,
            stubs=stubs,
            render_markdown=render_markdown,
            get_backlinks=lambda _url: [],
            content_root=tmp_path,
        )
        assert adapted.content_ir is not None
        assert adapted.content_ir.headings
        assert adapted.body_html
        assert adapted.body_text
        assert adapted.sections
        assert any(section.heading == "Section" for section in adapted.sections)


class TestCatalogGraphV3:
    def test_export_includes_v3_fields(self) -> None:
        node = DocNode(
            url="/docs/test/",
            slug="docs/test",
            title="Test",
            description="Desc",
            layout="doc",
            weight=1,
            section="test",
            tags=frozenset({"alpha"}),
            body_md="# Test\n",
            body_html="<h1>Test</h1>",
            toc=(),
            source_path="docs/test.md",
            content_format="patitas-markdown",
            body_text="Test body",
            sections=(),
        )

        class _Catalog:
            active_channel = "latest"
            nodes = (node,)

            def doc_nodes(self):
                return [node]

            def backlinks_for(self, _node):
                return []

            def graph_edges(self):
                return []

            def namespaces(self):
                return []

        payload = catalog_graph(_Catalog(), schema_version=3)
        assert payload["schema_version"] == 3
        page = payload["pages"][0]
        assert page["content_format"] == "patitas-markdown"
        assert page["body_source"] == "# Test"
        assert page["body_text"] == "Test body"
        assert page["body_md"] == "# Test"

    def test_live_scan_exports_filesystem_provider_provenance(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        page_path = content / "docs" / "hello.md"
        page_path.parent.mkdir(parents=True)
        page_path.write_text("---\ntitle: Hello\n---\n\n# Hello\n", encoding="utf-8")
        catalog = DocCatalog(content, autodoc=False, mount="docs")

        payload = catalog_graph(catalog, schema_version=3)
        page = payload["pages"][0]
        assert page["source_provider"] == "filesystem"
        assert page["provenance"]["provider"] == "filesystem"
        assert page["provenance"]["path"] == "docs/hello.md"
        assert page["provenance"]["mount"] == "docs"

    def test_live_scan_applies_catalog_identity_defaults(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        page_path = content / "docs" / "hello.md"
        page_path.parent.mkdir(parents=True)
        page_path.write_text("---\ntitle: Hello\n---\n\n# Hello\n", encoding="utf-8")
        catalog = DocCatalog(
            content,
            autodoc=False,
            mount="docs",
            identity_meta={
                "tenant": "acme",
                "workspace": "platform",
                "site": "developer-docs",
            },
        )

        payload = catalog_graph(catalog, schema_version=3)
        page = payload["pages"][0]
        assert page["tenant"] == "acme"
        assert page["workspace"] == "platform"
        assert page["site"] == "developer-docs"
        assert page["provenance"]["tenant"] == "acme"
        assert page["provenance"]["workspace"] == "platform"
        namespace = payload["namespaces"][0]
        assert namespace["tenant"] == "acme"
        assert namespace["workspace"] == "platform"
        assert namespace["site"] == "developer-docs"

    def test_frontmatter_identity_overrides_catalog_defaults(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        page_path = content / "docs" / "hello.md"
        page_path.parent.mkdir(parents=True)
        page_path.write_text(
            "---\ntitle: Hello\ntenant: beta\nworkspace: support\nsite: kb\n---\n\n# Hello\n",
            encoding="utf-8",
        )
        catalog = DocCatalog(
            content,
            autodoc=False,
            mount="docs",
            identity_meta={
                "tenant": "acme",
                "workspace": "platform",
                "site": "developer-docs",
            },
        )

        page = catalog_graph(catalog, schema_version=3)["pages"][0]
        assert page["tenant"] == "beta"
        assert page["workspace"] == "support"
        assert page["site"] == "kb"

    def test_v3_export_includes_provenance_and_owner_fields(self) -> None:
        node = DocNode(
            url="/docs/test/",
            slug="docs/test",
            title="Test",
            description="",
            layout="doc",
            weight=1,
            section="test",
            tags=frozenset(),
            body_md="# Test",
            body_html="<h1>Test</h1>",
            toc=(),
            source_path="docs/test.md",
            meta={
                "owner": "docs-platform",
                "source_provider": "git",
                "source_repo": "lbliii/furatena",
                "source_ref": "main",
                "tenant": "default",
                "site": "docs",
            },
            mount="docs",
            edition="latest",
        )

        class _Catalog:
            active_channel = "latest"
            nodes = (node,)

            def doc_nodes(self):
                return [node]

            def backlinks_for(self, _node):
                return []

            def graph_edges(self):
                return []

            def namespaces(self):
                return []

        payload = catalog_graph(_Catalog(), schema_version=3)
        page = payload["pages"][0]
        assert page["source_provider"] == "git"
        assert page["source_repo"] == "lbliii/furatena"
        assert page["source_ref"] == "main"
        assert page["owner"] == "docs-platform"
        assert page["team"] == "docs-platform"
        assert page["tenant"] == "default"
        assert page["site"] == "docs"
        assert page["output_channel"] == "latest"
        assert page["provenance"]["path"] == "docs/test.md"
        assert page["provenance"]["mount"] == "docs"

    def test_meta_json_preserves_provenance_for_static_impact_reports(self) -> None:
        node = DocNode(
            url="/docs/test/",
            slug="docs/test",
            title="Test",
            description="",
            layout="doc",
            weight=1,
            section="test",
            tags=frozenset({"impact"}),
            body_md="# Test",
            body_html="<h1>Test</h1>",
            toc=(),
            source_path="docs/test.md",
            meta={
                "owner": "docs-platform",
                "team": "docs-infra",
                "source_provider": "git",
                "source_repo": "lbliii/furatena",
                "source_ref": "main",
                "generated_from": "specs/openapi.yaml",
                "tenant": "default",
                "workspace": "platform",
                "site": "docs",
                "last_indexed_at": "2026-06-29T18:00:00Z",
            },
            mount="docs",
            edition="latest",
        )

        class _Catalog:
            active_channel = "stable"
            nodes = (node,)

        payload = meta_json(_Catalog())
        page = payload["pages"][0]
        assert page["source_path"] == "docs/test.md"
        assert page["source_provider"] == "git"
        assert page["source_repo"] == "lbliii/furatena"
        assert page["source_ref"] == "main"
        assert page["generated_from"] == "specs/openapi.yaml"
        assert page["owner"] == "docs-platform"
        assert page["team"] == "docs-infra"
        assert page["tenant"] == "default"
        assert page["workspace"] == "platform"
        assert page["site"] == "docs"
        assert page["output_channel"] == "stable"
        assert page["last_indexed_at"] == "2026-06-29T18:00:00Z"
        assert page["provenance"] == {
            "provider": "git",
            "repo": "lbliii/furatena",
            "ref": "main",
            "source_url": None,
            "path": "docs/test.md",
            "generated_from": "specs/openapi.yaml",
            "owner": "docs-platform",
            "team": "docs-infra",
            "mount": "docs",
            "edition": "latest",
            "tenant": "default",
            "workspace": "platform",
            "site": "docs",
            "output_channel": "stable",
            "last_indexed_at": "2026-06-29T18:00:00Z",
        }

    def test_v2_compat_export(self) -> None:
        node = DocNode(
            url="/docs/test/",
            slug="docs/test",
            title="Test",
            description="",
            layout="doc",
            weight=1,
            section="test",
            tags=frozenset(),
            body_md="Body",
            body_html="<p>Body</p>",
            toc=(),
            source_path="docs/test.md",
        )

        class _Catalog:
            active_channel = "latest"
            nodes = (node,)

            def doc_nodes(self):
                return [node]

            def backlinks_for(self, _node):
                return []

            def graph_edges(self):
                return []

            def namespaces(self):
                return []

        payload = catalog_graph(_Catalog(), schema_version=2)
        assert payload["schema_version"] == 2
        page = payload["pages"][0]
        assert "body_md" in page
        assert "content_format" not in page

    def test_live_catalog_indexes_site_content(self) -> None:
        content_root = REPO / "content" / "chirp"
        if not content_root.is_dir():
            return
        catalog = DocCatalog(content_root, autodoc=False)
        assert catalog.nodes
        sample = catalog.doc_nodes()[0]
        assert sample.content_format == "patitas-markdown"
        assert sample.body_text or sample.body_html


class TestHtmlAdapter:
    def test_extracts_headings_and_links(self) -> None:
        adapter = get_content_adapter("html")
        html = """
        <h1>Guide</h1>
        <p>See <a href="/docs/other/">Other</a>.</p>
        <h2>Details</h2>
        <div data-component="callout" data-tone="info">Note</div>
        """
        _doc, content_ir = adapter.parse(html)
        assert content_ir is not None
        assert any(h.text == "Guide" for h in content_ir.headings)
        assert any(link.href == "/docs/other/" for link in content_ir.links)
        assert any(ext.name == "callout" for ext in content_ir.directives)

    def test_catalog_indexes_html_file(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "legacy.html").write_text(
            "---\ntitle: Legacy\n---\n<h1>Legacy</h1><p>Still valid.</p>",
            encoding="utf-8",
        )
        config = MountSourceConfig.from_mount_dict({"extensions": [".html"]})
        catalog = DocCatalog(tmp_path, autodoc=False, source_config=config)
        node = catalog.get_by_slug("docs/legacy")
        assert node is not None
        assert node.content_format == "html"
        assert "Legacy" in node.body_html


class TestRstAdapter:
    def test_rst_compatibility_reports_roles_and_directives(self) -> None:
        from furatena.catalog.format_compat import rst_compatibility_findings

        source = (
            "Guide\n"
            "=====\n\n"
            ".. note:: Supported note.\n\n"
            ".. tabs::\n\n"
            "See :py:class:`example.Client`.\n"
        )
        findings = rst_compatibility_findings(source, source_path="docs/guide.rst")
        by_construct = {finding.construct: finding for finding in findings}
        assert by_construct["RST directive '.. note::'"].severity == "info"
        assert by_construct["RST directive '.. note::'"].line == 3
        assert by_construct["RST directive '.. tabs::'"].severity == "warning"
        assert by_construct["RST directive '.. tabs::'"].line == 5
        assert by_construct["RST role ':py:class:'"].severity == "warning"
        assert by_construct["RST role ':py:class:'"].line == 8
        assert (
            "not resolved through Furatena inventories"
            in by_construct["RST role ':py:class:'"].behavior
        )

    def test_extracts_rst_structure(self) -> None:
        pytest = __import__("pytest")
        docutils = pytest.importorskip("docutils")
        _ = docutils
        adapter = get_content_adapter("docutils-rst")
        source = "Title\n=====\n\n`Other </docs/other/>`_\n\nSection\n-------\n\nBody text.\n"
        _doc, content_ir = adapter.parse(source)
        assert content_ir is not None
        assert content_ir.headings
        assert content_ir.links
        assert content_ir.links[0].line == 4

    def test_catalog_indexes_rst_file(self, tmp_path: Path) -> None:
        pytest = __import__("pytest")
        pytest.importorskip("docutils")
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "guide.rst").write_text(
            "---\ntitle: Guide\n---\n\nGuide\n=====\n\nHello RST.\n",
            encoding="utf-8",
        )
        config = MountSourceConfig.from_mount_dict({"extensions": [".rst"]})
        catalog = DocCatalog(tmp_path, autodoc=False, source_config=config)
        node = catalog.get_by_slug("docs/guide")
        assert node is not None
        assert node.content_format == "docutils-rst"
        assert "Hello RST" in node.body_text


class TestMdxAdapter:
    def test_mdx_compatibility_reports_mapped_and_unsupported_jsx(self) -> None:
        from furatena.catalog.directives.registry import create_directive_registry
        from furatena.catalog.format_compat import mdx_compatibility_findings

        source = '# MDX\n\n<Cards columns="2">Body</Cards>\n\n<ApiTable endpoint="/v1" />\n'
        findings = mdx_compatibility_findings(
            source,
            source_path="docs/page.mdx",
            known_directives=create_directive_registry().names,
        )
        by_construct = {finding.construct: finding for finding in findings}
        assert by_construct["MDX JSX component <Cards>"].severity == "info"
        assert by_construct["MDX JSX component <Cards>"].line == 3
        assert (
            "mapped to Patitas directive 'cards'"
            in by_construct["MDX JSX component <Cards>"].behavior
        )
        assert by_construct["MDX JSX component <ApiTable>"].severity == "warning"
        assert by_construct["MDX JSX component <ApiTable>"].line == 5
        assert (
            "unregistered Patitas directive"
            in by_construct["MDX JSX component <ApiTable>"].behavior
        )

    def test_lowers_jsx_to_markdown_extensions(self) -> None:
        from furatena.catalog.sources.adapters.mdx import mdx_to_markdown

        source = '# Title\n\n<Callout tone="info">Hello</Callout>\n'
        lowered = mdx_to_markdown(source)
        assert ":::callout" in lowered
        assert "Hello" in lowered

    def test_catalog_indexes_mdx_file(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "page.mdx").write_text(
            "---\ntitle: MDX Page\n---\n\n# MDX Page\n\nPlain text.\n",
            encoding="utf-8",
        )
        config = MountSourceConfig.from_mount_dict({"extensions": [".mdx"]})
        catalog = DocCatalog(tmp_path, autodoc=False, source_config=config)
        node = catalog.get_by_slug("docs/page")
        assert node is not None
        assert node.content_format == "mdx"
        assert node.body_html

    def test_check_warns_for_unsupported_mdx_jsx(self, tmp_path: Path) -> None:
        from furatena.catalog.check import check_catalog

        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "page.mdx").write_text(
            '---\ntitle: MDX Page\n---\n\n# MDX Page\n\n<ApiTable endpoint="/v1" />\n',
            encoding="utf-8",
        )
        config = MountSourceConfig.from_mount_dict({"extensions": [".mdx"]})
        catalog = DocCatalog(tmp_path, autodoc=False, source_config=config)
        errors, warnings = check_catalog(catalog)

        assert errors == []
        assert any(
            "docs/page.mdx:3: MDX JSX component <ApiTable>" in warning for warning in warnings
        )


class TestMystAdapter:
    def test_lowers_myst_directives_and_roles_to_markdown(self) -> None:
        from furatena.catalog.sources.adapters.myst import myst_to_markdown

        source = (
            "# Guide\n\n"
            "```{note}\n"
            "Body.\n"
            "```\n\n"
            "See {ref}`Section <section-target>` and {doc}`Other <docs/other>`.\n"
        )
        lowered = myst_to_markdown(source)
        assert ":::{note}" in lowered
        assert "[Section](#section-target)" in lowered
        assert "[Other](/docs/other/)" in lowered

    def test_myst_compatibility_reports_mapped_and_unsupported_constructs(self) -> None:
        from furatena.catalog.directives.registry import create_directive_registry
        from furatena.catalog.format_compat import myst_compatibility_findings

        source = (
            "# Guide\n\n"
            "```{note}\n"
            "Body.\n"
            "```\n\n"
            "```{unknown-panel}\n"
            "Body.\n"
            "```\n\n"
            "See {ref}`Section <section-target>` and {term}`Content IR`.\n"
        )
        findings = myst_compatibility_findings(
            source,
            source_path="docs/guide.myst",
            known_directives=create_directive_registry().names,
        )
        by_construct = {finding.construct: finding for finding in findings}
        assert by_construct["MyST directive '{note}'"].severity == "info"
        assert by_construct["MyST directive '{note}'"].line == 3
        assert by_construct["MyST directive '{unknown-panel}'"].severity == "warning"
        assert by_construct["MyST directive '{unknown-panel}'"].line == 7
        assert by_construct["MyST role '{ref}'"].severity == "info"
        assert by_construct["MyST role '{term}'"].severity == "warning"
        assert "not mapped to Content IR" in by_construct["MyST role '{term}'"].behavior

    def test_catalog_indexes_myst_fixture_corpus(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "other.myst").write_text("---\ntitle: Other\n---\n\n# Other\n", encoding="utf-8")
        (docs / "guide.myst").write_text(
            "---\ntitle: MyST Guide\n---\n\n"
            "# MyST Guide\n\n"
            "```{note}\n"
            "Admonition body.\n"
            "```\n\n"
            "```{tabs}\n"
            "Tab body.\n"
            "```\n\n"
            "See {ref}`Section <section-target>` and {doc}`Other <docs/other>`.\n\n"
            "```python\n"
            "print('ok')\n"
            "```\n\n"
            "## Section {#section-target}\n",
            encoding="utf-8",
        )
        config = MountSourceConfig.from_mount_dict({"extensions": [".myst"]})
        catalog = DocCatalog(tmp_path, autodoc=False, source_config=config)
        node = catalog.get_by_slug("docs/guide")
        assert node is not None
        assert node.content_format == "myst-markdown"
        assert node.content_ir is not None
        assert any(heading.text.startswith("MyST Guide") for heading in node.content_ir.headings)
        assert {directive.name for directive in node.content_ir.directives} >= {"note", "tabs"}
        assert {link.href for link in node.content_ir.links} >= {"#section-target", "/docs/other/"}
        assert "print" in node.body_text

    def test_check_warns_for_unsupported_myst_constructs(self, tmp_path: Path) -> None:
        from furatena.catalog.check import check_catalog

        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "guide.myst").write_text(
            "---\ntitle: MyST Guide\n---\n\n"
            "# MyST Guide\n\n"
            "```{unknown-panel}\n"
            "Body.\n"
            "```\n\n"
            "See {term}`Content IR`.\n",
            encoding="utf-8",
        )
        config = MountSourceConfig.from_mount_dict({"extensions": [".myst"]})
        catalog = DocCatalog(tmp_path, autodoc=False, source_config=config)
        errors, warnings = check_catalog(catalog)

        assert errors == []
        assert any(
            "docs/guide.myst:3: MyST directive '{unknown-panel}'" in warning for warning in warnings
        )
        assert any("docs/guide.myst:7: MyST role '{term}'" in warning for warning in warnings)
