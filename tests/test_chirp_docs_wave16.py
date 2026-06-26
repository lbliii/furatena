"""Tests for Wave 16 — boosted links, inventory HTTP, strict edition checks."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.check import check_cross_edition_links
from furatena.catalog.inventories.export import inventories_json, inventory_bytes
from furatena.catalog.inventories.models import InventoryEntry, InventorySpec
from furatena.catalog.inventories.sphinx import parse_objects_inv_bytes
from furatena.catalog.inventories.store import InventoryStore
from furatena.catalog.link_lint import check_body_link_boost, shell_link_attrs
from furatena.catalog.links import boost_internal_links
from furatena.catalog.models import DocNode


def _node(
    *,
    slug: str,
    url: str,
    edition: str = "latest",
    body_html: str = "",
    body_md: str = "",
) -> DocNode:
    return DocNode(
        url=url,
        slug=slug,
        title=slug.rsplit("/", 1)[-1],
        description="",
        layout="doc",
        weight=0,
        section="docs",
        tags=frozenset(),
        body_md=body_md,
        body_html=body_html,
        toc=(),
        source_path=f"{slug}.md",
        meta={},
        mount="chirp",
        edition=edition,
    )


class TestBoostedLinkAudit:
    def test_shell_link_attrs_boosts_internal_href(self) -> None:
        html = '<p><a href="/docs/foo/">Foo</a></p>'
        boosted = str(boost_internal_links(html, shell_link_attrs))
        assert "hx-boost" in boosted

    def test_check_body_link_boost_passes_after_filter(self) -> None:
        node = _node(
            slug="docs/page",
            url="/docs/page/",
            body_html='<p><a href="/docs/foo/">Foo</a></p>',
        )
        catalog = type("CatalogStub", (), {"nodes": (node,)})()
        errors, warnings = check_body_link_boost(catalog)
        assert not errors
        assert not warnings


class TestInventoryHttpExport:
    def test_inventory_bytes_roundtrip(self) -> None:
        store = InventoryStore(
            entries={
                "doc:docs/page": InventoryEntry(
                    domain="doc",
                    name="docs/page",
                    objtype="doc",
                    uri="/docs/page/",
                    display_name="Page",
                    priority=1,
                    inventory_id="local-catalog",
                )
            },
            specs=(InventorySpec(id="local-catalog", format="dcp-catalog"),),
        )
        catalog = type("CatalogStub", (), {"inventory_store": store, "active_channel": "latest"})()
        raw = inventory_bytes(catalog, "local-catalog")
        assert raw is not None
        parsed = parse_objects_inv_bytes(raw, inventory_id="local-catalog")
        assert len(parsed) == 1

    def test_inventories_json_lists_specs(self) -> None:
        store = InventoryStore(
            entries={},
            specs=(InventorySpec(id="local-catalog", format="dcp-catalog", mount="chirp"),),
        )
        catalog = type("CatalogStub", (), {"inventory_store": store, "active_channel": "latest"})()
        payload = inventories_json(catalog, base_url="https://docs.example.com")
        assert payload["default_inventory"] == "local-catalog"
        assert payload["objects_inv_url"].endswith("/objects.inv")
        assert payload["inventories"][0]["url"].endswith("/inventories/local-catalog/objects.inv")


class TestStrictEditionLinks:
    def test_cross_edition_strict_is_error(self) -> None:
        source = _node(
            slug="docs/source",
            url="/docs/source/",
            edition="latest",
            body_md="[Target](/docs/target/)",
        )
        target = _node(slug="docs/target", url="/docs/target/", edition="v1")

        class _Catalog:
            nodes = (source, target)

            def get(self, url: str):
                if url == "/docs/target/":
                    return target
                if url == "/docs/source/":
                    return source
                return None

        errors, warnings = check_cross_edition_links(_Catalog(), strict=True)
        assert errors
        assert not warnings
