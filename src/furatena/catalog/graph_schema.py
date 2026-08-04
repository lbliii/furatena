"""Typed graph schema for the documentation catalog."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from furatena.catalog.record_types import EdgeRecord, GraphNodeRecord, NamespaceRecord

if TYPE_CHECKING:
    from furatena.catalog.loader import DocCatalog
    from furatena.catalog.models import DocNode


class EdgeKind(StrEnum):
    """Relationship kinds in the documentation graph."""

    API_AUTH = "api_auth"
    API_ENVIRONMENT = "api_environment"
    API_EXAMPLE = "api_example"
    API_REQUEST_BODY = "api_request_body"
    API_RESPONSE = "api_response"
    API_SCHEMA = "api_schema"
    API_TAG = "api_tag"
    AVAILABLE_IN = "available_in"
    BREAKS = "breaks"
    EXPLAINS = "explains"
    GENERATED_FROM = "generated_from"
    IMPLEMENTS = "implements"
    NAV_NEXT = "nav_next"
    NAV_PREV = "nav_prev"
    OWNED_BY = "owned_by"
    PARENT = "parent"
    LINK = "link"
    REQUIRES = "requires"
    SUPERSEDES = "supersedes"
    TAG = "tag"
    TRANSLATION = "translation"
    VALIDATES = "validates"


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """One directed edge in the catalog graph."""

    kind: EdgeKind
    source: str
    target: str
    mount: str = "chirp"
    edition: str = "latest"


_GRAPH_NODE_PREFIXES: dict[str, str] = {
    "api": "api_operation",
    "api-tag": "api_tag",
    "auth": "api_auth",
    "environment": "api_environment",
    "example": "api_example",
    "request-body": "api_request_body",
    "release": "release",
    "response": "api_response",
    "schema": "api_schema",
    "source": "source_file",
}


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


_META_EDGE_KEYS: dict[str, EdgeKind] = {
    "available_in": EdgeKind.AVAILABLE_IN,
    "available-in": EdgeKind.AVAILABLE_IN,
    "api_auth": EdgeKind.API_AUTH,
    "api-auth": EdgeKind.API_AUTH,
    "api_auth_schemes": EdgeKind.API_AUTH,
    "api-auth-schemes": EdgeKind.API_AUTH,
    "api_environments": EdgeKind.API_ENVIRONMENT,
    "api-environments": EdgeKind.API_ENVIRONMENT,
    "api_examples": EdgeKind.API_EXAMPLE,
    "api-examples": EdgeKind.API_EXAMPLE,
    "api_request_bodies": EdgeKind.API_REQUEST_BODY,
    "api-request-bodies": EdgeKind.API_REQUEST_BODY,
    "api_responses": EdgeKind.API_RESPONSE,
    "api-responses": EdgeKind.API_RESPONSE,
    "api_schemas": EdgeKind.API_SCHEMA,
    "api-schemas": EdgeKind.API_SCHEMA,
    "api_tags": EdgeKind.API_TAG,
    "api-tags": EdgeKind.API_TAG,
    "auth": EdgeKind.API_AUTH,
    "auth_schemes": EdgeKind.API_AUTH,
    "auth-schemes": EdgeKind.API_AUTH,
    "breaks": EdgeKind.BREAKS,
    "environments": EdgeKind.API_ENVIRONMENT,
    "explains": EdgeKind.EXPLAINS,
    "examples": EdgeKind.API_EXAMPLE,
    "generated_from": EdgeKind.GENERATED_FROM,
    "generated-from": EdgeKind.GENERATED_FROM,
    "implements": EdgeKind.IMPLEMENTS,
    "request_bodies": EdgeKind.API_REQUEST_BODY,
    "request-bodies": EdgeKind.API_REQUEST_BODY,
    "requires": EdgeKind.REQUIRES,
    "responses": EdgeKind.API_RESPONSE,
    "schemas": EdgeKind.API_SCHEMA,
    "supersedes": EdgeKind.SUPERSEDES,
    "validates": EdgeKind.VALIDATES,
}

_EXTERNAL_TARGET_PREFIXES: dict[EdgeKind, str] = {
    EdgeKind.API_AUTH: "auth",
    EdgeKind.API_ENVIRONMENT: "environment",
    EdgeKind.API_EXAMPLE: "example",
    EdgeKind.API_REQUEST_BODY: "request-body",
    EdgeKind.API_RESPONSE: "response",
    EdgeKind.API_SCHEMA: "schema",
    EdgeKind.API_TAG: "api-tag",
    EdgeKind.AVAILABLE_IN: "release",
}


def _iter_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        stripped = value.strip()
        return (stripped,) if stripped else ()
    if isinstance(value, dict):
        for key in ("id", "node_id", "slug", "url", "path", "name"):
            item = value.get(key)
            if item:
                return (str(item).strip(),)
        return ()
    if isinstance(value, (list, tuple, set, frozenset)):
        values: list[str] = []
        for item in value:
            values.extend(_iter_values(item))
        return tuple(values)
    return (str(value).strip(),)


def _external_target(value: str, *, default_prefix: str) -> str:
    if ":" in value:
        return value
    return f"{default_prefix}:{value}"


def _semantic_target(value: str, catalog: DocCatalog, id_by_url: dict[str, str]) -> str:
    if value.startswith("/"):
        return id_by_url.get(value if value.endswith("/") else f"{value}/", value)
    node = catalog.get_by_slug(value.strip("/"))
    if node is not None:
        return node.node_id
    if value.endswith(
        (
            ".json",
            ".md",
            ".mdx",
            ".rst",
            ".html",
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".yaml",
            ".yml",
        )
    ):
        return _external_target(value, default_prefix="source")
    return value if ":" in value else f"ref:{value}"


def _append_edge(
    edges: list[GraphEdge],
    seen: set[tuple[str, str, str, str, str]],
    edge: GraphEdge,
) -> None:
    key = (edge.kind.value, edge.source, edge.target, edge.mount, edge.edition)
    if key in seen:
        return
    seen.add(key)
    edges.append(edge)


def build_graph_edges(
    catalog: DocCatalog,
    *,
    url_index: dict[str, str] | None = None,
    nodes_by_id: dict[str, DocNode] | None = None,
) -> list[GraphEdge]:
    """Build typed edges for all nodes in a catalog shard."""
    edges: list[GraphEdge] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    local_nodes_by_id = {node.node_id: node for node in catalog.nodes}
    resolved_nodes_by_id = nodes_by_id or local_nodes_by_id
    id_by_url = url_index or {node.url: node.node_id for node in catalog.nodes}

    for node in catalog.nodes:
        parent_slug = _parent_slug(node.slug)
        if parent_slug is not None:
            parent = catalog.get_by_slug(parent_slug)
            if parent is not None and parent.mount == node.mount and parent.edition == node.edition:
                _append_edge(
                    edges,
                    seen,
                    GraphEdge(
                        kind=EdgeKind.PARENT,
                        source=node.node_id,
                        target=parent.node_id,
                        mount=node.mount,
                        edition=node.edition,
                    ),
                )

        for tag in sorted(node.tags):
            _append_edge(
                edges,
                seen,
                GraphEdge(
                    kind=EdgeKind.TAG,
                    source=node.node_id,
                    target=f"tag:{tag}",
                    mount=node.mount,
                    edition=node.edition,
                ),
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
            _append_edge(
                edges,
                seen,
                GraphEdge(
                    kind=EdgeKind.LINK,
                    source=node.node_id,
                    target=target_id,
                    mount=node.mount,
                    edition=node.edition,
                ),
            )

        prev_node, next_node = catalog.prev_next(node)
        if prev_node is not None and prev_node.node_id in local_nodes_by_id:
            _append_edge(
                edges,
                seen,
                GraphEdge(
                    kind=EdgeKind.NAV_PREV,
                    source=node.node_id,
                    target=prev_node.node_id,
                    mount=node.mount,
                    edition=node.edition,
                ),
            )
        if next_node is not None and next_node.node_id in local_nodes_by_id:
            _append_edge(
                edges,
                seen,
                GraphEdge(
                    kind=EdgeKind.NAV_NEXT,
                    source=node.node_id,
                    target=next_node.node_id,
                    mount=node.mount,
                    edition=node.edition,
                ),
            )

        owner = str(node.meta.get("owner") or node.meta.get("team") or "").strip()
        if owner:
            _append_edge(
                edges,
                seen,
                GraphEdge(
                    kind=EdgeKind.OWNED_BY,
                    source=node.node_id,
                    target=_external_target(owner, default_prefix="owner"),
                    mount=node.mount,
                    edition=node.edition,
                ),
            )

        source_path = str(getattr(node, "source_path", "") or "").strip()
        if source_path:
            _append_edge(
                edges,
                seen,
                GraphEdge(
                    kind=EdgeKind.GENERATED_FROM,
                    source=node.node_id,
                    target=_external_target(source_path, default_prefix="source"),
                    mount=node.mount,
                    edition=node.edition,
                ),
            )

        for meta_key, kind in _META_EDGE_KEYS.items():
            for value in _iter_values(node.meta.get(meta_key)):
                target = (
                    _external_target(value, default_prefix=_EXTERNAL_TARGET_PREFIXES[kind])
                    if kind in _EXTERNAL_TARGET_PREFIXES
                    else _semantic_target(value, catalog, id_by_url)
                )
                _append_edge(
                    edges,
                    seen,
                    GraphEdge(
                        kind=kind,
                        source=node.node_id,
                        target=target,
                        mount=node.mount,
                        edition=node.edition,
                    ),
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


def edge_record(edge: GraphEdge) -> EdgeRecord:
    return {
        "kind": edge.kind.value,
        "source": edge.source,
        "target": edge.target,
        "mount": edge.mount,
        "edition": edge.edition,
    }


def graph_node_records(
    edges: list[EdgeRecord],
    *,
    edition_statuses: dict[tuple[str, str], str] | None = None,
) -> list[GraphNodeRecord]:
    """Project external edge endpoints into typed graph node records."""
    records: dict[tuple[str, str, str], GraphNodeRecord] = {}
    for edge in edges:
        for endpoint in ("source", "target"):
            value = str(edge.get(endpoint) or "")
            prefix, separator, label = value.partition(":")
            if not separator:
                continue
            kind = _GRAPH_NODE_PREFIXES.get(prefix)
            if kind is None:
                continue
            mount = str(edge.get("mount") or "")
            edition = str(edge.get("edition") or "")
            key = (value, mount, edition)
            record: GraphNodeRecord = {
                "id": value,
                "kind": kind,
                "label": label,
                "mount": mount,
                "edition": edition,
            }
            if edition_statuses is not None:
                record["edition_status"] = edition_statuses.get((mount, edition), "current")
            records.setdefault(key, record)
    return sorted(
        records.values(),
        key=lambda item: (item["kind"], item["id"], item["mount"], item["edition"]),
    )


def namespace_record(
    mount_id: str,
    label: str,
    *,
    edition: str,
    page_count: int,
    tenant: str | None = None,
    workspace: str | None = None,
    site: str | None = None,
    edition_status: str = "current",
    release_date: str | None = None,
    end_of_life: str | None = None,
    banner: str | None = None,
) -> NamespaceRecord:
    record: NamespaceRecord = {
        "mount": mount_id,
        "edition": edition,
        "label": label,
        "page_count": page_count,
        "edition_status": edition_status,
    }
    if release_date:
        record["release_date"] = release_date
    if end_of_life:
        record["end_of_life"] = end_of_life
    if banner:
        record["banner"] = banner
    if tenant:
        record["tenant"] = tenant
    if workspace:
        record["workspace"] = workspace
    if site:
        record["site"] = site
    return record
