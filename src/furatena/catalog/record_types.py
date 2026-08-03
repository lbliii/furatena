"""Typed JSON records crossing catalog, query, export, and MCP boundaries."""

from __future__ import annotations

from typing import Any, TypedDict


class ProvenanceRecord(TypedDict, total=False):
    provider: str
    repo: str | None
    ref: str | None
    source_url: str | None
    path: str
    generated_from: str | None
    owner: str | None
    team: str | None
    mount: str
    edition: str
    tenant: str | None
    workspace: str | None
    site: str | None
    output_channel: str
    last_indexed_at: str | None


class PageRecord(TypedDict, total=False):
    node_id: str
    url: str
    slug: str
    title: str
    description: str
    section: str
    weight: int | float
    tags: list[str]
    source_path: str
    source: str
    doc_version: Any
    mount: str
    edition: str
    lang: str
    translation_key: str
    section_root: bool
    source_provider: str
    source_repo: str | None
    source_ref: str | None
    source_url: str | None
    generated_from: str | None
    owner: str | None
    team: str | None
    tenant: str | None
    workspace: str | None
    site: str | None
    output_channel: str | None
    last_indexed_at: str | None
    provenance: ProvenanceRecord
    api_operation: Any
    api_try_it: Any
    toc: list[dict[str, Any]]
    backlinks: list[dict[str, str]]
    content_format: str
    source_kind: str
    content: dict[str, Any]
    body_md: str
    body_source: str
    body_text: str
    sections: list[dict[str, Any]]
    ast_path: str
    native_ast: dict[str, str]
    layout: str


class EdgeRecord(TypedDict):
    kind: str
    source: str
    target: str
    mount: str
    edition: str


class GraphNodeRecord(TypedDict):
    id: str
    kind: str
    label: str
    mount: str
    edition: str


class NamespaceRecord(TypedDict, total=False):
    mount: str
    edition: str
    label: str
    page_count: int
    tenant: str
    workspace: str
    site: str


class CatalogGraphRecord(TypedDict, total=False):
    schema_version: int
    version: int
    channel: str
    edition: str
    mount: str
    page_count: int
    pages: list[PageRecord]
    edges: list[EdgeRecord]
    graph_nodes: list[GraphNodeRecord]
    namespaces: list[NamespaceRecord]
    inventories: list[dict[str, Any]]
    structure_index: dict[str, Any]


class GraphQuerySpec(TypedDict):
    mount: str | None
    edition: str | None
    tag: str | None
    format: str | None
    owner: str | None
    locale: str | None
    edge_kind: str | None
    source: str | None
    target: str | None
    include_private: bool
    limit: int
    offset: int


class GraphQueryRecord(TypedDict, total=False):
    schema_version: int
    version: int
    channel: str | None
    edition: str | None
    query: GraphQuerySpec
    page_count: int
    edge_count: int
    total: int
    edge_total: int
    limit: int
    offset: int
    next_offset: int | None
    pages: list[PageRecord]
    edges: list[EdgeRecord]
    graph_nodes: list[GraphNodeRecord]
    namespaces: list[NamespaceRecord]


class SearchSectionRecord(TypedDict):
    id: str
    heading: str
    anchor: str
    depth: int
    body: str


class SearchEntryRecord(TypedDict, total=False):
    node_id: str
    url: str
    title: str
    description: str
    section: str
    snippet: str
    mount: str
    edition: str
    tags: list[str]
    lang: str
    translation_key: str
    provenance: ProvenanceRecord
    api_operation: dict[str, Any]
    sections: list[SearchSectionRecord]


class SearchIndexRecord(TypedDict):
    version: int
    channel: str
    base_url: str
    page_count: int
    facets: dict[str, list[str]]
    entries: list[SearchEntryRecord]


class MCPResourceContentRecord(TypedDict):
    uri: str
    mimeType: str
    text: str


class MCPToolResultRecord(TypedDict):
    content: list[dict[str, str]]
    structuredContent: dict[str, Any]
    isError: bool
