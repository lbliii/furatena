"""Typed record boundaries preserve serialized catalog/search bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import get_type_hints

from furatena.catalog.export import _page_record, catalog_graph, search_json
from furatena.catalog.graph_schema import edge_record, graph_node_records
from furatena.catalog.loader import DocCatalog
from furatena.catalog.mcp import FuraMCPServer
from furatena.catalog.models import DocNode
from furatena.catalog.query import query_catalog_graph
from furatena.catalog.record_types import (
    CatalogGraphRecord,
    EdgeRecord,
    GraphNodeRecord,
    GraphQueryRecord,
    MCPResourceContentRecord,
    MCPToolResultRecord,
    PageRecord,
    SearchIndexRecord,
)


def _stable_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def _type_hints(function):
    return get_type_hints(function, localns={"DocCatalog": DocCatalog})


def test_boundary_functions_publish_typed_records() -> None:
    assert _type_hints(_page_record)["return"] is PageRecord
    assert _type_hints(catalog_graph)["return"] is CatalogGraphRecord
    assert _type_hints(search_json)["return"] is SearchIndexRecord
    assert _type_hints(edge_record)["return"] is EdgeRecord
    assert _type_hints(graph_node_records)["return"] == list[GraphNodeRecord]
    assert _type_hints(query_catalog_graph)["return"] is GraphQueryRecord
    assert _type_hints(FuraMCPServer.read_resource)["return"] is MCPResourceContentRecord
    assert _type_hints(FuraMCPServer.call_tool)["return"] is MCPToolResultRecord


def test_catalog_and_search_serialization_match_golden_bytes() -> None:
    node = DocNode(
        url="/docs/guide/",
        slug="docs/guide",
        title="Guide",
        description="Stable guide.",
        layout="doc",
        weight=1,
        section="docs",
        tags=frozenset({"guide"}),
        body_md="# Guide\n\nStable body.",
        body_html="<h1>Guide</h1>",
        toc=(),
        source_path="docs/guide.md",
        meta={},
        mount="docs",
    )

    class Catalog:
        active_channel = "latest"
        nodes = (node,)
        _source_mtimes: dict[Path, float] = {}
        content_root = None

        def doc_nodes(self):
            return [node]

        def backlinks_for(self, _node):
            return []

        def graph_edges(self):
            return []

        def namespaces(self):
            return []

        def inventories_metadata(self):
            return []

    catalog = Catalog()

    catalog_bytes = _stable_bytes(catalog_graph(catalog))
    search_bytes = _stable_bytes(search_json(catalog, base_url="https://docs.example.com"))

    assert hashlib.sha256(catalog_bytes).hexdigest() == (
        "33ffbef4ae33ae642d12c1961ce4934f0302c51d9cc1ee487c0df617638d0af1"
    )
    assert hashlib.sha256(search_bytes).hexdigest() == (
        "308e9c65e1a92e3d76b959a73c48d6b7f94018da21d3e658b3b897043dc47887"
    )
