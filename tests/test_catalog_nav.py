"""Catalog outer-rail section discovery and docs.yaml overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from furatena.catalog.catalog_nav import (
    CatalogNavConfig,
    CatalogSectionConfig,
    parse_catalog_nav,
    resolve_doc_sections,
)
from furatena.catalog.models import DocNode


def _node(
    *,
    slug: str,
    title: str,
    section: str,
    weight: int = 100,
    icon: str | None = None,
) -> DocNode:
    meta = {"icon": icon} if icon else {}
    return DocNode(
        url=f"/{slug}/",
        slug=slug,
        title=title,
        description="",
        layout="doc",
        weight=weight,
        section=section,
        tags=frozenset(),
        body_md="",
        body_html="",
        toc=(),
        source_path=f"{slug}.md",
        meta=meta,
    )


class TestParseCatalogNav:
    def test_empty_when_missing(self) -> None:
        assert parse_catalog_nav(None) == CatalogNavConfig()
        assert parse_catalog_nav({}) == CatalogNavConfig()

    def test_string_and_object_entries(self) -> None:
        raw = {
            "sections": [
                "get-started",
                {"id": "concepts", "label": "Core ideas", "icon": "layers", "mark": "C"},
            ]
        }
        config = parse_catalog_nav(raw)
        assert config.sections == (
            CatalogSectionConfig(id="get-started"),
            CatalogSectionConfig(
                id="concepts",
                label="Core ideas",
                icon="layers",
                mark="C",
            ),
        )


class TestResolveDocSections:
    def test_auto_detects_sections_sorted_by_index_weight(self) -> None:
        sections_map = {
            "reference": [_node(slug="docs/reference/cli", title="CLI", section="reference")],
            "concepts": [_node(slug="docs/concepts/graph", title="Graph", section="concepts")],
        }
        indexes = {
            "docs/reference": _node(
                slug="docs/reference",
                title="Reference",
                section="reference",
                weight=60,
            ),
            "docs/concepts": _node(
                slug="docs/concepts",
                title="Concepts",
                section="concepts",
                weight=20,
                icon="layers",
            ),
        }

        resolved = resolve_doc_sections(
            sections_map=sections_map,
            slug_prefix="",
            nav_config=None,
            get_index_node=lambda slug: indexes.get(slug),
        )

        assert [item.id for item in resolved] == ["concepts", "reference"]
        assert resolved[0].label == "Concepts"
        assert resolved[0].icon == "layers"
        assert resolved[1].mark == "07"

    def test_yaml_order_pins_sections_and_appends_discovered(self) -> None:
        sections_map = {
            "about": [_node(slug="docs/about/team", title="Team", section="about")],
            "concepts": [_node(slug="docs/concepts/graph", title="Graph", section="concepts")],
            "authoring": [_node(slug="docs/authoring/nav", title="Nav", section="authoring")],
        }
        indexes = {
            "docs/about": _node(slug="docs/about", title="About", section="about", weight=90),
            "docs/concepts": _node(slug="docs/concepts", title="Concepts", section="concepts", weight=20),
            "docs/authoring": _node(
                slug="docs/authoring",
                title="Authoring",
                section="authoring",
                weight=30,
            ),
        }
        nav_config = CatalogNavConfig(
            sections=(
                CatalogSectionConfig(id="authoring"),
                CatalogSectionConfig(id="concepts", label="Core concepts"),
            )
        )

        resolved = resolve_doc_sections(
            sections_map=sections_map,
            slug_prefix="",
            nav_config=nav_config,
            get_index_node=lambda slug: indexes.get(slug),
        )

        assert [item.id for item in resolved] == ["authoring", "concepts", "about"]
        assert resolved[1].label == "Core concepts"


REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"


@pytest.fixture(scope="module")
def docs_config():
    from furatena.catalog.config import load_docs_config

    return load_docs_config(APP_ROOT / "docs.yaml")


class TestFuratenaCatalogRail:
    def test_docs_yaml_declares_furatena_sections(self, docs_config) -> None:
        section_ids = [item.id for item in docs_config.catalog.sections]
        assert section_ids == [
            "get-started",
            "concepts",
            "authoring",
            "theming",
            "operations",
            "reference",
            "about",
        ]

    def test_default_mount_rail_includes_furatena_sections(self, docs_config) -> None:
        from furatena.catalog.registry import CatalogRegistry

        registry = CatalogRegistry.from_config(
            docs_config.mounts_path,
            repo_root=REPO,
            app_root=APP_ROOT,
            rewrites_path=docs_config.rewrites_path,
            inventories_path=docs_config.inventories_path,
            autodoc=False,
            catalog_nav=docs_config.catalog,
        )
        default = next(m for m in registry.mounts if m.default)
        titles = [
            item["title"]
            for item in registry._shards[default.id].catalog_rail_items("/")
            if item["href"].startswith("/docs/")
        ]
        assert titles == [
            "Get Started",
            "Concepts",
            "Authoring",
            "Theming",
            "Operations",
            "Reference",
            "About",
        ]
