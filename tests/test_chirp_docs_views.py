"""View registry and view-kind configuration tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.config import load_docs_config
from furatena.catalog.loader import DocCatalog
from furatena.catalog.models import DocNode
from furatena.catalog.view_kinds import VIEW_KIND_BY_NAME, VIEW_KINDS
from furatena.catalog.views import ViewRegistry


@pytest.fixture(scope="module")
def docs_config():
    return load_docs_config(APP_ROOT / "docs.yaml")


@pytest.fixture(scope="module")
def views(docs_config) -> ViewRegistry:
    return ViewRegistry(docs_config)


@pytest.fixture(scope="module")
def catalog() -> DocCatalog:
    return DocCatalog(REPO / "content" / "chirp", autodoc=False, autodoc_config=None)


class TestViewKinds:
    def test_builtin_kinds_registered(self) -> None:
        kinds = {spec.kind for spec in VIEW_KINDS}
        assert kinds >= {"doc", "doc_list", "home", "collection", "api_reference", "portal"}

    def test_docs_yaml_maps_all_builtin_kinds(self, docs_config) -> None:
        for spec in VIEW_KINDS:
            if spec.template_key == "home":
                continue
            assert spec.template_key in docs_config.views, f"missing views.{spec.template_key}"

    def test_surface_for_catalog_doc_view(self, views: ViewRegistry) -> None:
        assert views.surface("views/doc.html") == "catalog"
        assert views.surface("views/api_reference.html") == "catalog"
        assert views.surface("views/home.html") == "app"

    def test_validate_config_is_clean(self, views: ViewRegistry) -> None:
        assert views.validate_config() == []


class TestViewResolution:
    def test_explicit_view_meta_wins(self, views: ViewRegistry) -> None:
        node = DocNode(
            url="/demo/",
            slug="demo",
            title="Demo",
            description="",
            layout="doc",
            weight=100,
            section="demo",
            tags=frozenset(),
            body_md="",
            body_html="",
            toc=(),
            source_path="demo.md",
            meta={"view": "views/page.html"},
        )
        assert views.resolve(node) == "views/page.html"

    def test_view_kind_alias_on_node(self) -> None:
        node = DocNode(
            url="/x/",
            slug="x",
            title="X",
            description="",
            layout="collection",
            weight=0,
            section="x",
            tags=frozenset(),
            body_md="",
            body_html="",
            toc=(),
            source_path="x.md",
        )
        assert node.view_kind == "collection"

    def test_section_root_doc_resolves_to_doc_list(
        self, views: ViewRegistry, catalog: DocCatalog
    ) -> None:
        node = catalog.get_by_slug("docs/tutorials")
        assert node is not None
        assert views.resolve(node, catalog) == "views/doc_list.html"

    def test_compose_only_for_collection_kind(
        self, views: ViewRegistry, catalog: DocCatalog
    ) -> None:
        doc = catalog.get_by_slug("docs/get-started/installation")
        collection = catalog.get_by_slug("docs/get-started/read-through")
        assert doc is not None and collection is not None
        assert views.compose(doc, catalog) == {}
        ctx = views.compose(collection, catalog)
        assert ctx.get("collection") is not None
        assert len(ctx.get("collection_sections") or []) >= 2

    def test_kind_spec_lookup(self, views: ViewRegistry) -> None:
        spec = views.kind_spec("doc")
        assert spec is not None
        assert spec.surface == "catalog"
        assert VIEW_KIND_BY_NAME["home"].surface == "app"
