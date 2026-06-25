"""Tests for Wave 15 — edition xrefs, inventory export, AST chunks."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.inventories.dcp import catalog_doc_inventory_entries
from furatena.catalog.inventories.models import InventoryEntry
from furatena.catalog.inventories.sphinx import parse_objects_inv_bytes, write_objects_inv_bytes
from furatena.catalog.loader import DocCatalog
from furatena.catalog.models import ContentHeading, ContentIR, DocNode
from furatena.catalog.references.resolver import resolve_reference
from furatena.catalog.registry import CatalogRegistry, MountConfig


def _node(slug: str, url: str, *, mount: str = "chirp", edition: str = "latest") -> DocNode:
    return DocNode(
        url=url,
        slug=slug,
        title=slug.rsplit("/", 1)[-1],
        description="",
        layout="doc",
        weight=0,
        section="docs",
        tags=frozenset(),
        body_md="",
        body_html="",
        toc=(),
        source_path=f"{slug}.md",
        meta={},
        mount=mount,
        edition=edition,
    )


class TestEditionAwareResolver:
    def test_mount_edition_slug(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "guide.md").write_text("---\ntitle: Guide\n---\n\nBody\n", encoding="utf-8")
        registry = CatalogRegistry(
            (
                MountConfig(
                    id="chirp",
                    label="Chirp",
                    content_root=tmp_path,
                    default=True,
                ),
            ),
            repo_root=tmp_path,
            autodoc=False,
        )
        resolved = resolve_reference(
            "chirp:latest:docs/guide",
            catalog=registry,
            inventory_store=None,
            role_name="xref",
        )
        assert resolved.resolved
        assert resolved.href == "/docs/guide/"
        assert resolved.edition == "latest"


class TestInventoryExport:
    def test_write_and_parse_roundtrip(self) -> None:
        entries = (
            InventoryEntry(
                domain="doc",
                name="docs/page",
                objtype="doc",
                uri="/docs/page/",
                display_name="Page",
                priority=1,
                inventory_id="local-catalog",
            ),
        )
        raw = write_objects_inv_bytes(entries, project="test", version="1")
        parsed = parse_objects_inv_bytes(raw, inventory_id="local-catalog")
        assert len(parsed) == 1
        assert parsed[0].name == "docs/page"

    def test_catalog_doc_inventory_entries(self, tmp_path: Path) -> None:
        (tmp_path / "docs" / "page.md").parent.mkdir(parents=True)
        (tmp_path / "docs" / "page.md").write_text(
            "---\ntitle: Page\n---\n\nBody\n",
            encoding="utf-8",
        )
        catalog = DocCatalog(tmp_path, autodoc=False, mount="chirp")
        entries = catalog_doc_inventory_entries(
            list(catalog.nodes),
            inventory_id="local-catalog",
            mount="chirp",
        )
        assert any(entry.name == "docs/page" for entry in entries)


class TestAstChunks:
    def test_chunks_from_content_ir_headings(self) -> None:
        from furatena.catalog.chunks import chunk_node

        content_ir = ContentIR(
            headings=(
                ContentHeading(level=1, text="Title", anchor="title", line=1),
                ContentHeading(level=2, text="Section", anchor="section", line=3),
            ),
        )
        body = "# Title\n\nIntro.\n\n## Section\n\nDetails.\n"
        node = DocNode(
            url="/docs/page/",
            slug="docs/page",
            title="Page",
            description="",
            layout="doc",
            weight=0,
            section="docs",
            tags=frozenset(),
            body_md=body,
            body_html="",
            toc=(),
            source_path="docs/page.md",
            meta={},
            content_ir=content_ir,
        )
        chunks = chunk_node(node)
        assert any(chunk.heading == "Section" for chunk in chunks)
