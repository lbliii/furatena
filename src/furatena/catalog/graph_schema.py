"""Typed graph schema for the documentation catalog."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from furatena.catalog.loader import DocCatalog
    from furatena.catalog.models import DocNode


class EdgeKind(StrEnum):
    """Relationship kinds in the documentation graph."""

    PARENT = "parent"
    LINK = "link"
    NAV_NEXT = "nav_next"
    NAV_PREV = "nav_prev"
    TAG = "tag"
    TRANSLATION = "translation"


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """One directed edge in the catalog graph."""

    kind: EdgeKind
    source: str
    target: str
    mount: str = "chirp"
    edition: str = "latest"


def make_node_id(mount: str, edition: str, slug: str) -> str:
    """Stable node identifier across mounts and editions."""
    normalized = slug.strip("/") or "index"
    return f"{mount}:{edition}:{normalized}"


def parse_node_id(node_id: str) -> tuple[str, str, str]:
    """Split ``mount:edition:slug`` into its parts."""
    mount, edition, slug = node_id.split(":", 2)
    return mount, edition, slug


def _is_index_source_path(source_path: str) -> bool:
    name = source_path.rsplit("/", 1)[-1]
    return name in {
        "_index.md",
        "index.html",
        "index.rst",
        "_index.rst",
    }


def is_section_root(node: DocNode) -> bool:
    """Whether a node is a first-class section root."""
    if node.section_root:
        return True
    if node.meta.get("section_root"):
        return True
    return _is_index_source_path(node.source_path) and node.slug.count("/") >= 1


def infer_section_root(*, meta: dict[str, Any], slug: str, source_path: str) -> bool:
    if meta.get("section_root"):
        return True
    return _is_index_source_path(source_path) and slug.count("/") >= 1


def _parent_slug(slug: str) -> str | None:
    slug = slug.strip("/")
    if not slug or "/" not in slug:
        return None
    return slug.rsplit("/", 1)[0]


def build_graph_edges(
    catalog: DocCatalog,
    *,
    url_index: dict[str, str] | None = None,
    nodes_by_id: dict[str, DocNode] | None = None,
) -> list[GraphEdge]:
    """Build typed edges for all nodes in a catalog shard."""
    edges: list[GraphEdge] = []
    local_nodes_by_id = {node.node_id: node for node in catalog.nodes}
    resolved_nodes_by_id = nodes_by_id or local_nodes_by_id
    id_by_url = url_index or {node.url: node.node_id for node in catalog.nodes}

    for node in catalog.nodes:
        parent_slug = _parent_slug(node.slug)
        if parent_slug is not None:
            parent = catalog.get_by_slug(parent_slug)
            if parent is not None and parent.mount == node.mount and parent.edition == node.edition:
                edges.append(
                    GraphEdge(
                        kind=EdgeKind.PARENT,
                        source=node.node_id,
                        target=parent.node_id,
                        mount=node.mount,
                        edition=node.edition,
                    )
                )

        edges.extend(
            GraphEdge(
                kind=EdgeKind.TAG,
                source=node.node_id,
                target=f"tag:{tag}",
                mount=node.mount,
                edition=node.edition,
            )
            for tag in sorted(node.tags)
        )

        from furatena.catalog.graph import extract_page_links

        link_urls = extract_page_links(node, catalog=catalog)
        for target_url in link_urls:
            target_id = id_by_url.get(target_url)
            if target_id is None:
                continue
            target_node = resolved_nodes_by_id.get(target_id)
            if target_node is None:
                continue
            if target_node.edition != node.edition:
                continue
            edges.append(
                GraphEdge(
                    kind=EdgeKind.LINK,
                    source=node.node_id,
                    target=target_id,
                    mount=node.mount,
                    edition=node.edition,
                )
            )

        prev_node, next_node = catalog.prev_next(node)
        if prev_node is not None and prev_node.node_id in local_nodes_by_id:
            edges.append(
                GraphEdge(
                    kind=EdgeKind.NAV_PREV,
                    source=node.node_id,
                    target=prev_node.node_id,
                    mount=node.mount,
                    edition=node.edition,
                )
            )
        if next_node is not None and next_node.node_id in local_nodes_by_id:
            edges.append(
                GraphEdge(
                    kind=EdgeKind.NAV_NEXT,
                    source=node.node_id,
                    target=next_node.node_id,
                    mount=node.mount,
                    edition=node.edition,
                )
            )

    return edges


def build_translation_edges(
    nodes: tuple[DocNode, ...],
) -> list[GraphEdge]:
    """Link pages that share a ``translation_key`` across locales."""
    groups: dict[str, list[DocNode]] = {}
    for node in nodes:
        key = node.translation_key
        if not key:
            continue
        groups.setdefault(key, []).append(node)

    edges: list[GraphEdge] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        anchor = sorted(group, key=lambda item: (item.lang, item.url))[0]
        for node in group:
            if node.node_id == anchor.node_id:
                continue
            edges.append(
                GraphEdge(
                    kind=EdgeKind.TRANSLATION,
                    source=node.node_id,
                    target=anchor.node_id,
                    mount=node.mount,
                    edition=node.edition,
                )
            )
    return edges


def edge_record(edge: GraphEdge) -> dict[str, Any]:
    return {
        "kind": edge.kind.value,
        "source": edge.source,
        "target": edge.target,
        "mount": edge.mount,
        "edition": edge.edition,
    }


def namespace_record(mount_id: str, label: str, *, edition: str, page_count: int) -> dict[str, Any]:
    return {
        "mount": mount_id,
        "edition": edition,
        "label": label,
        "page_count": page_count,
    }
