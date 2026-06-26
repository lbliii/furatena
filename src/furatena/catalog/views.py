"""View resolution and compose context for catalog nodes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml

from furatena.catalog.config import DocsConfig
from furatena.catalog.graph_schema import is_section_root
from furatena.catalog.toc import collection_toc_items
from furatena.catalog.view_kinds import (
    DEFAULT_APP_VIEW_TEMPLATES,
    DEFAULT_CATALOG_VIEW_TEMPLATES,
    VIEW_KIND_BY_NAME,
    VIEW_KINDS,
    Surface,
    ViewKindSpec,
)

if TYPE_CHECKING:
    from furatena.catalog.models import DocNode
    from furatena.catalog.registry import CatalogRegistry


class CatalogLike(Protocol):
    def get_by_slug(self, slug: str, *, mount: str | None = None) -> DocNode | None: ...

    def body_html(self, node: DocNode) -> str: ...

    def direct_child_count(
        self,
        slug: str,
        *,
        lang: str | None = None,
        mount: str | None = None,
    ) -> int: ...


@dataclass(frozen=True, slots=True)
class CollectionDef:
    """One ordered group of catalog nodes for a collection view."""

    id: str
    title: str
    description: str
    nodes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CollectionSection:
    """Resolved catalog node inside a collection view."""

    index: int
    slug: str
    node: DocNode
    body_html: str
    anchor: str


class ViewRegistry:
    """Resolve catalog nodes to view templates and enrich compose views."""

    def __init__(self, config: DocsConfig) -> None:
        self.config = config
        self._collections_path = (
            config.compose.get("collection").data
            if "collection" in config.compose
            else None
        )
        self._collections = self._load_collections(self._collections_path)

    @staticmethod
    def _load_collections(path: Path | None) -> dict[str, CollectionDef]:
        if path is None or not path.is_file():
            return {}
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        collections: dict[str, CollectionDef] = {}
        for collection_id, item in raw.items():
            if not isinstance(item, dict):
                continue
            nodes_raw = item.get("nodes") or item.get("items") or ()
            nodes = tuple(str(slug).strip("/") for slug in nodes_raw if slug)
            collections[str(collection_id)] = CollectionDef(
                id=str(collection_id),
                title=str(item.get("title") or collection_id),
                description=str(item.get("description") or ""),
                nodes=nodes,
            )
        return collections

    def resolve(self, node: DocNode, catalog: CatalogLike | None = None) -> str:
        """Pick the view template for a catalog node."""
        meta = node.meta
        explicit = meta.get("view") or meta.get("template")
        if explicit:
            key = str(explicit)
            mapped = self.config.views.get(key)
            if mapped:
                return mapped
            stem = key.removesuffix(".html")
            mapped = self.config.views.get(stem)
            if mapped:
                return mapped
            return key

        if node.url == "/" or node.slug in ("", "index"):
            home_view = self.config.views.get("home")
            if home_view:
                return home_view

        slug_override = self.config.overrides.get(node.slug)
        if slug_override:
            return slug_override

        view_kind = node.layout or "doc"
        if view_kind == "doc" and catalog is not None and is_section_root(node):
            child_count = catalog.direct_child_count(node.slug, mount=node.mount)
            if child_count > 0:
                doc_list = self.config.views.get("doc_list")
                if doc_list:
                    return doc_list

        return self.config.views.get(view_kind) or self.config.views.get("default") or "views/doc.html"

    def surface(self, view_template: str) -> Surface:
        """Return ``app`` or ``catalog`` surface for a resolved view template."""
        for spec in VIEW_KINDS:
            mapped = self.config.views.get(spec.template_key) or spec.default_template
            if mapped == view_template:
                return spec.surface
        if view_template in DEFAULT_CATALOG_VIEW_TEMPLATES:
            return "catalog"
        if view_template in DEFAULT_APP_VIEW_TEMPLATES:
            return "app"
        return "app"

    def kind_spec(self, view_kind: str) -> ViewKindSpec | None:
        """Look up built-in metadata for a view kind name."""
        return VIEW_KIND_BY_NAME.get(view_kind)

    def compose(self, node: DocNode, catalog: CatalogLike) -> dict[str, Any]:
        """Extra template context for multi-node views."""
        if node.layout != "collection":
            return {}
        return self._compose_collection(node, catalog)

    def validate_config(self) -> list[str]:
        """Return human-readable config warnings (empty when valid)."""
        warnings: list[str] = []
        for key, template in self.config.views.items():
            if key == "default":
                continue
            if key not in VIEW_KIND_BY_NAME and not str(template).startswith("views/"):
                warnings.append(
                    f"views.{key} is a custom entry — document its surface and compose needs in VIEWS.md"
                )
        for slug, template in self.config.overrides.items():
            if template not in self.config.views.values() and not str(template).startswith("views/"):
                warnings.append(f"overrides.{slug} points to unknown template: {template}")
        collection = self.config.compose.get("collection")
        if collection is not None and collection.data is not None and not collection.data.is_file():
            warnings.append(f"compose.collection.data missing: {collection.data}")
        return warnings

    def has_collection(self, collection_id: str) -> bool:
        """Return whether a collection id exists in compose data."""
        return str(collection_id) in self._collections

    def _compose_collection(self, node: DocNode, catalog: CatalogLike) -> dict[str, Any]:
        collection_id = (
            node.meta.get("collection")
            or node.meta.get("collection_id")
            or node.slug.rsplit("/", 1)[-1]
        )
        definition = self._collections.get(str(collection_id))
        if definition is None:
            return {
                "collection": None,
                "collection_sections": [],
                "collection_error": f"Unknown collection: {collection_id}",
                "toc_items": [],
                "toc_panel_title": node.title,
                "toc_panel_eyebrow": "In this collection",
            }

        sections: list[CollectionSection] = []
        for index, slug in enumerate(definition.nodes, start=1):
            member = catalog.get_by_slug(slug, mount=node.mount)
            if member is None:
                continue
            sections.append(
                CollectionSection(
                    index=index,
                    slug=slug,
                    node=member,
                    body_html=catalog.body_html(member),
                    anchor=f"collection-section-{index}",
                )
            )

        return {
            "collection": definition,
            "collection_sections": sections,
            "collection_error": None,
            "toc_items": collection_toc_items(sections),
            "toc_panel_title": definition.title,
            "toc_panel_eyebrow": "In this collection",
        }
