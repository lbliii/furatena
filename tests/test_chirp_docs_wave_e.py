"""Tests for Wave E — federation, inventories, and reference resolution."""

from __future__ import annotations

import sys
import textwrap
import zlib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.check import check_unresolved_references
from furatena.catalog.export import catalog_graph
from furatena.catalog.inventories.sphinx import parse_objects_inv_bytes
from furatena.catalog.references.resolver import resolve_reference
from furatena.catalog.registry import CatalogRegistry, MountConfig
from furatena.catalog.rewrites import RewriteTable, load_rewrite_table_from_dict
from furatena.catalog.sources import FilesystemScanner, MountSourceConfig


def _make_objects_inv(*lines: str) -> bytes:
    header = textwrap.dedent(
        """\
        # Sphinx inventory version 2
        # Project: test
        # Version: 1.0
        # The remainder of this file is compressed with zlib.
        """
    ).encode("utf-8")
    body = "\n".join(lines).encode("utf-8")
    return header + b"\n" + zlib.compress(body)


class TestRewriteTable:
    def test_longest_prefix_match(self) -> None:
        table = load_rewrite_table_from_dict(
            {
                "prefixes": [
                    {"from": "/chirp/docs/", "to": "/docs/"},
                    {"from": "/chirp/", "to": "/"},
                ]
            }
        )
        assert table.rewrite("/chirp/docs/get-started/") == "/docs/get-started/"
        assert table.rewrite("/chirp/portal/") == "/portal/"

    def test_defaults_when_empty(self) -> None:
        table = RewriteTable()
        assert table.rewrite("/chirp/docs/foo/") == "/docs/foo/"


class TestSphinxInventoryParser:
    def test_parse_objects_inv_bytes(self) -> None:
        raw = _make_objects_inv("os.path.join py:function library/os.html -")
        entries = parse_objects_inv_bytes(raw, inventory_id="python-stdlib")
        assert len(entries) == 1
        entry = entries[0]
        assert entry.name == "os.path.join"
        assert entry.domain == "py"
        assert entry.inventory_id == "python-stdlib"


class TestMountQualifiedWikilinks:
    def test_resolve_shared_mount_target(self, tmp_path: Path) -> None:
        shared_root = tmp_path / "shared"
        shared_page = shared_root / "reference" / "target.md"
        shared_page.parent.mkdir(parents=True)
        shared_page.write_text("---\ntitle: Target\n---\n\nBody\n", encoding="utf-8")

        chirp_root = tmp_path / "chirp" / "docs"
        chirp_root.mkdir(parents=True)
        source = chirp_root / "linker.md"
        source.write_text(
            "---\ntitle: Linker\n---\n\nSee [[shared:reference/target|Target]].\n",
            encoding="utf-8",
        )

        shared_scanner = FilesystemScanner(MountSourceConfig())
        shared_pages = shared_scanner.scan(shared_root, url_prefix="/shared")
        federated = {f"shared:{page.slug}": page.url for page in shared_pages}

        chirp_scanner = FilesystemScanner(MountSourceConfig())
        chirp_pages = chirp_scanner.scan(tmp_path / "chirp", url_prefix="")
        slug_to_url = chirp_scanner.build_slug_to_url(chirp_pages)
        resolved = chirp_scanner.resolve_wikilinks(
            chirp_pages,
            slug_to_url,
            federated_slug_urls=federated,
        )
        assert "[Target](/shared/reference/target/)" in resolved[0].body


class TestFederatedBacklinks:
    def test_cross_mount_backlinks(self, tmp_path: Path) -> None:
        mounts_yaml = tmp_path / "mounts.yaml"
        shared_root = tmp_path / "shared" / "reference"
        shared_root.mkdir(parents=True)
        (shared_root / "target.md").write_text(
            "---\ntitle: Target\n---\n\nTarget page.\n",
            encoding="utf-8",
        )
        chirp_root = tmp_path / "site" / "content" / "docs"
        chirp_root.mkdir(parents=True)
        (chirp_root / "linker.md").write_text(
            "---\ntitle: Linker\n---\n\nSee [Target](/shared/reference/target/).\n",
            encoding="utf-8",
        )
        mounts_yaml.write_text(
            yaml.safe_dump(
                {
                    "mounts": [
                        {
                            "id": "chirp",
                            "label": "Chirp",
                            "content_root": str(tmp_path / "site" / "content"),
                            "default": True,
                        },
                        {
                            "id": "shared",
                            "label": "Shared",
                            "content_root": str(tmp_path / "shared"),
                            "url_prefix": "/shared",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

        registry = CatalogRegistry.from_config(
            mounts_yaml,
            repo_root=tmp_path,
            app_root=tmp_path,
            autodoc=False,
        )
        target = registry.get("/shared/reference/target/")
        assert target is not None
        backlinks = registry.backlinks_for(target)
        assert any(ref["title"] == "Linker" for ref in backlinks)


class TestReferenceResolver:
    def test_mount_qualified_slug(self, tmp_path: Path) -> None:
        content = tmp_path / "docs"
        content.mkdir()
        (content / "get-started.md").write_text(
            "---\ntitle: Get Started\n---\n\nBody\n",
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
        resolved = resolve_reference(
            "chirp:docs/get-started",
            catalog=registry,
            inventory_store=None,
            role_name="xref",
        )
        assert resolved.resolved
        assert resolved.href == "/get-started/"

    def test_inventory_role_lookup(self) -> None:
        from furatena.catalog.inventories.models import InventoryEntry
        from furatena.catalog.inventories.store import InventoryStore

        store = InventoryStore(
            entries={
                "py:os.path.join": InventoryEntry(
                    domain="py",
                    name="os.path.join",
                    objtype="function",
                    uri="https://docs.python.org/3/library/os.html#os.path.join",
                    display_name="os.path.join",
                    priority=1,
                    inventory_id="python-stdlib",
                )
            },
            role_domains={"py": "python-stdlib"},
        )
        resolved = resolve_reference(
            "os.path.join",
            catalog=None,
            inventory_store=store,
            role_name="py",
        )
        assert resolved.resolved
        assert resolved.href.startswith("https://docs.python.org/")


class TestCatalogExportInventories:
    def test_catalog_graph_includes_inventory_metadata(self, tmp_path: Path) -> None:
        content = tmp_path / "docs"
        content.mkdir()
        (content / "page.md").write_text("---\ntitle: Page\n---\n\nBody\n", encoding="utf-8")
        inventories = tmp_path / "inventories.yaml"
        inventories.write_text(
            yaml.safe_dump(
                {
                    "role_domains": {"doc": "local"},
                    "inventories": [{"id": "local", "format": "dcp-catalog", "mount": "chirp"}],
                }
            ),
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
            app_root=tmp_path,
            inventories_path=inventories,
            autodoc=False,
        )
        payload = catalog_graph(registry)
        assert "inventories" in payload
        assert payload["inventories"][0]["id"] == "local"


class TestUnresolvedReferenceCheck:
    def test_flags_missing_xref(self, tmp_path: Path) -> None:
        content = tmp_path / "docs"
        content.mkdir()
        (content / "broken.md").write_text(
            "---\ntitle: Broken\n---\n\nSee {xref}`missing:nowhere`.\n",
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
        errors = check_unresolved_references(registry)
        assert any("unresolved reference" in err for err in errors)
