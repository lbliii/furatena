"""Wave 11 tests for content lint, directive contracts, and front matter."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog import DocCatalog
from furatena.catalog.check import check_catalog, check_content_lint, check_front_matter
from furatena.catalog.config import load_docs_config
from furatena.catalog.content_lint import lint_page_ast
from furatena.catalog.directives.registry import create_directive_registry
from furatena.catalog.render import DocsRenderer
from furatena.catalog.views import ViewRegistry


class TestContentLintRules:
    def test_heading_increment_violation(self) -> None:
        renderer = DocsRenderer()
        registry = create_directive_registry()
        document, content_ir = renderer.parse("# One\n\n### Three\n")
        errors, warnings = lint_page_ast(
            document,
            content_ir,
            registry,
            source="page.md",
            body="# One\n\n### Three\n",
        )
        assert not errors
        assert any("Heading level skipped" in warning for warning in warnings)

    def test_empty_link_warning(self) -> None:
        renderer = DocsRenderer()
        registry = create_directive_registry()
        document, content_ir = renderer.parse("[](/docs/target/)\n")
        errors, warnings = lint_page_ast(
            document,
            content_ir,
            registry,
            source="page.md",
        )
        assert not errors
        assert any("Link has no visible text" in warning for warning in warnings)


class TestDirectiveContracts:
    def test_step_outside_steps_is_error(self) -> None:
        renderer = DocsRenderer()
        registry = create_directive_registry()
        document, content_ir = renderer.parse(":::{step}\nDo thing\n:::\n")
        errors, _warnings = lint_page_ast(
            document,
            content_ir,
            registry,
            source="page.md",
        )
        assert any("'step' must be inside" in error for error in errors)

    def test_tab_item_outside_tab_set_is_error(self) -> None:
        renderer = DocsRenderer()
        registry = create_directive_registry()
        document, content_ir = renderer.parse(":::{tab-item}\nPanel\n:::\n")
        errors, _warnings = lint_page_ast(
            document,
            content_ir,
            registry,
            source="page.md",
        )
        assert any("'tab-item' must be inside" in error for error in errors)

    def test_valid_steps_pass(self) -> None:
        renderer = DocsRenderer()
        registry = create_directive_registry()
        source = ":::{steps}\n:::{step}\nOne\n:::\n:::{step}\nTwo\n:::\n:::\n"
        document, content_ir = renderer.parse(source)
        errors, warnings = lint_page_ast(
            document,
            content_ir,
            registry,
            source="page.md",
        )
        assert not errors
        assert not warnings


class TestFrontMatterLint:
    def test_unknown_collection_is_error(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        page = docs / "missing-collection.md"
        page.write_text(
            "---\ntitle: Bad\nlayout: collection\ncollection: does-not-exist\n---\n# Bad\n",
            encoding="utf-8",
        )
        catalog = DocCatalog(content, autodoc=False, autodoc_config=None)
        config = load_docs_config(APP_ROOT / "docs.yaml")
        views = ViewRegistry(config)
        errors, _warnings = check_front_matter(catalog, views=views)
        assert any("unknown collection" in error for error in errors)


class TestCheckIntegration:
    def test_content_lint_on_minimal_catalog(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        good = docs / "good.md"
        good.write_text(
            "---\ntitle: Good\n---\n# Good\n\nSee [Target](/docs/target/).\n",
            encoding="utf-8",
        )
        target = docs / "target.md"
        target.write_text("---\ntitle: Target\n---\n# Target\n", encoding="utf-8")

        catalog = DocCatalog(content, autodoc=False, autodoc_config=None)
        errors, warnings = check_content_lint(catalog)
        assert not errors
        assert isinstance(warnings, list)

    def test_check_catalog_runs_all_layers(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        page = docs / "broken.md"
        page.write_text(
            "---\ntitle: Broken\n---\n# Broken\n\nSee [Missing](/docs/missing/).\n",
            encoding="utf-8",
        )

        catalog = DocCatalog(content, autodoc=False, autodoc_config=None)
        config = load_docs_config(APP_ROOT / "docs.yaml")
        views = ViewRegistry(config)
        errors, warnings = check_catalog(catalog, views=views)
        assert any("broken internal link" in error for error in errors)
        assert isinstance(warnings, list)

    def test_lifecycle_checks_include_skipped_draft_sources(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        public = docs / "public.md"
        public.write_text(
            "---\ntitle: Public\n---\n# Public\n\nSee [Secret](/docs/secret/).\n",
            encoding="utf-8",
        )
        draft = docs / "secret.md"
        draft.write_text(
            "---\ntitle: Secret\ndraft: true\npublished_at: 2026-06-26\n---\n# Secret\n",
            encoding="utf-8",
        )

        catalog = DocCatalog(content, autodoc=False, autodoc_config=None)
        errors, _warnings = check_catalog(catalog)

        assert any("draft pages cannot set published_at" in error for error in errors)
        assert any("public page links to draft/private target" in error for error in errors)

    def test_lifecycle_public_state_warns_without_publish_metadata(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        page = docs / "public.md"
        page.write_text(
            "---\ntitle: Public\nvisibility: public\n---\n# Public\n",
            encoding="utf-8",
        )

        catalog = DocCatalog(content, autodoc=False, autodoc_config=None)
        _errors, warnings = check_catalog(catalog)

        assert any("public lifecycle pages should set published_at" in warning for warning in warnings)
