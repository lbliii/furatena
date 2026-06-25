"""Serialize the doc catalog for agents and static export."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from furatena.catalog.content_ir import content_ir_record
from furatena.catalog.patitas_bridge import excerpt_text, llm_text, plain_text, section_texts
from furatena.catalog.search import search_nodes
from furatena.catalog.text import sections_record

if TYPE_CHECKING:
    from furatena.catalog.loader import DocCatalog


class CatalogExport(Protocol):
    active_channel: str
    nodes: tuple[Any, ...]

    def doc_nodes(self) -> list[Any]: ...
    def backlinks_for(self, node: Any) -> list[dict[str, str]]: ...
    def graph_edges(self) -> list[dict[str, Any]]: ...
    def namespaces(self) -> list[dict[str, Any]]: ...
    def inventories_metadata(self) -> list[dict[str, Any]]: ...


def catalog_graph(
    catalog: CatalogExport | DocCatalog,
    *,
    schema_version: int = 3,
) -> dict[str, Any]:
    """JSON-serializable view of the documentation graph."""
    pages: list[dict[str, Any]] = []
    for node in catalog.nodes:
        pages.append(_page_record(catalog, node, schema_version=schema_version))
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "version": schema_version,
        "channel": catalog.active_channel,
        "edition": catalog.active_channel,
        "page_count": len(pages),
        "pages": pages,
        "edges": catalog.graph_edges(),
        "namespaces": catalog.namespaces(),
    }
    inventories = catalog.inventories_metadata() if hasattr(catalog, "inventories_metadata") else []
    if inventories:
        payload["inventories"] = inventories
    from furatena.catalog.structure_index import build_structure_index

    structure = build_structure_index(catalog)
    payload["structure_index"] = {
        "schema_version": structure["schema_version"],
        "directive_count": structure["directive_count"],
        "heading_count": structure["heading_count"],
        "directive_names": structure["directive_names"],
    }
    return payload


def _page_record(
    catalog: CatalogExport | DocCatalog,
    node,
    *,
    schema_version: int = 3,
) -> dict[str, Any]:
    source_kind = node.meta.get("source", "markdown")
    record: dict[str, Any] = {
        "node_id": node.node_id,
        "url": node.url,
        "slug": node.slug,
        "title": node.title,
        "description": node.description,
        "section": node.section,
        "weight": node.weight,
        "tags": sorted(node.tags),
        "source_path": node.source_path,
        "source": source_kind,
        "doc_version": node.meta.get("doc_version"),
        "mount": node.mount,
        "edition": node.edition,
        "lang": node.lang,
        "translation_key": node.translation_key,
        "section_root": node.section_root,
        "toc": [
            {"anchor": entry.anchor, "text": entry.text, "depth": entry.depth}
            for entry in node.toc
        ],
        "backlinks": catalog.backlinks_for(node),
    }
    if schema_version >= 3:
        record["content_format"] = node.content_format
        record["source_kind"] = "generated" if source_kind != "markdown" else "filesystem"
    content = content_ir_record(node.content_ir, schema_version=schema_version)
    if content is not None:
        record["content"] = content

    body_source = (node.body_source or "").strip()
    if body_source:
        record["body_md"] = body_source
        if schema_version >= 3:
            record["body_source"] = body_source
    if schema_version >= 3 and node.body_text.strip():
        record["body_text"] = node.body_text.strip()
    if schema_version >= 3 and node.sections:
        record["sections"] = sections_record(node.sections)
    if node.ast_json:
        slug_path = node.slug or "index"
        ast_path = f"{slug_path}.json"
        record["ast_path"] = ast_path
        if schema_version >= 3:
            record["native_ast"] = {
                "format": node.content_format,
                "path": ast_path,
            }
    return record


def meta_json(catalog: CatalogExport | DocCatalog) -> dict[str, Any]:
    """Compact per-page metadata index for agents and static export."""
    pages: list[dict[str, Any]] = []
    for node in catalog.nodes:
        pages.append(
            {
                "node_id": node.node_id,
                "url": node.url,
                "slug": node.slug,
                "title": node.title,
                "description": node.description,
                "section": node.section,
                "weight": node.weight,
                "tags": sorted(node.tags),
                "mount": node.mount,
                "edition": node.edition,
                "layout": node.layout,
                "section_root": node.section_root,
            }
        )
    return {
        "schema_version": 1,
        "channel": catalog.active_channel,
        "page_count": len(pages),
        "pages": pages,
    }


def surface_json() -> dict[str, Any]:
    """Machine-readable view-kind / surface registry."""
    from furatena.catalog.view_kinds import VIEW_KINDS

    return {
        "schema_version": 1,
        "views": [
            {
                "kind": spec.kind,
                "template": spec.default_template,
                "surface": spec.surface,
                "compose": spec.compose,
                "description": spec.description,
                "required_context": sorted(spec.required_context),
                "required_blocks": sorted(spec.required_blocks),
                "optional_context": sorted(spec.optional_context),
            }
            for spec in VIEW_KINDS
        ],
    }


def llms_full_txt(catalog: DocCatalog, *, site_name: str = "Furatena") -> str:
    """Full LLM-safe corpus for agents (Patitas ``render_llm`` when AST is available)."""
    lines = [f"# {site_name} Documentation (full corpus)", ""]
    documents = catalog.ast_documents() if hasattr(catalog, "ast_documents") else getattr(catalog, "_ast_documents", None)
    for node in catalog.doc_nodes():
        lines.extend((f"## {node.title}", ""))
        if node.description.strip():
            lines.extend((node.description.strip(), ""))
        document = None
        if isinstance(documents, dict):
            document = documents.get(node.node_id) or documents.get(node.slug)
        body = llm_text(node, document, source=node.body_md)
        if body:
            lines.extend((body, ""))
        lines.extend(("---", ""))
    return "\n".join(lines).rstrip() + "\n"


def search_json(catalog: DocCatalog, *, base_url: str = "") -> dict[str, Any]:
    """Machine-readable search index for tools and agents."""
    entries: list[dict[str, Any]] = []
    sections: set[str] = set()
    tags: set[str] = set()
    languages: set[str] = set()

    nodes = catalog.all_doc_nodes() if hasattr(catalog, "all_doc_nodes") else catalog.doc_nodes()
    documents = catalog.ast_documents() if hasattr(catalog, "ast_documents") else getattr(catalog, "_ast_documents", None)
    for node in nodes:
        sections.add(node.section)
        tags.update(node.tags)
        languages.add(getattr(node, "lang", "en"))
        document = None
        if isinstance(documents, dict):
            document = documents.get(node.node_id) or documents.get(node.slug)
        body_text = node.body_text.strip() or plain_text(node, document, source=node.body_md) or node.description
        blocks = []
        if node.sections:
            for section in node.sections:
                blocks.append(
                    {
                        "id": section.id,
                        "heading": section.heading,
                        "anchor": section.id,
                        "depth": section.depth,
                        "body": section.text[:500],
                    }
                )
        elif document is not None:
            for anchor, heading, section_body in section_texts(document, source=node.body_md):
                blocks.append(
                    {
                        "id": anchor,
                        "heading": heading,
                        "anchor": anchor,
                        "depth": 2,
                        "body": section_body[:500],
                    }
                )
        if not blocks:
            blocks = [
                {
                    "id": entry.anchor,
                    "heading": entry.text,
                    "anchor": entry.anchor,
                    "depth": entry.depth,
                    "body": excerpt_text(
                        node,
                        document,
                        source=node.body_md,
                        max_chars=500,
                    ),
                }
                for entry in node.toc
            ]
        entry: dict[str, Any] = {
            "url": _absolute_url(base_url, node.url),
            "title": node.title,
            "description": node.description,
            "section": node.section,
            "snippet": body_text[:240],
            "tags": sorted(node.tags),
            "lang": getattr(node, "lang", "en"),
        }
        if getattr(node, "translation_key", None):
            entry["translation_key"] = node.translation_key
        if blocks:
            entry["sections"] = blocks
        entries.append(entry)

    return {
        "version": 1,
        "channel": catalog.active_channel,
        "base_url": base_url.rstrip("/"),
        "page_count": len(entries),
        "facets": {
            "section": sorted(sections),
            "tags": sorted(tags),
            "lang": sorted(languages),
        },
        "entries": entries,
    }


def search_json_for_query(
    catalog: DocCatalog,
    query: str,
    *,
    base_url: str = "",
    limit: int = 12,
) -> dict[str, Any]:
    """Ranked search results in the same schema as ``search_json`` entries."""
    hits = search_nodes(
        catalog.doc_nodes(),
        query,
        limit=limit,
        documents=catalog.ast_documents() if hasattr(catalog, "ast_documents") else getattr(catalog, "_ast_documents", None),
    )
    return {
        "version": 1,
        "query": query,
        "count": len(hits),
        "results": [
            {
                "url": _absolute_url(base_url, hit.node.url),
                "title": hit.node.title,
                "description": hit.node.description,
                "section": hit.node.section,
                "snippet": hit.snippet,
                "score": hit.score,
            }
            for hit in hits
        ],
    }


def tools_manifest(catalog: DocCatalog, *, base_url: str = "", site_name: str = "Furatena") -> dict[str, Any]:
    """Stable MCP-style tool schema over the documentation catalog."""
    origin = base_url.rstrip("/")
    tool_slug = "-".join(part for part in site_name.lower().split() if part) or "furatena"
    return {
        "schema_version": 1,
        "name": f"{tool_slug}-docs",
        "description": f"Search and retrieve {site_name} documentation from the live catalog graph.",
        "catalog_url": f"{origin}/catalog.json" if origin else "/catalog.json",
        "search_url": f"{origin}/search.json" if origin else "/search.json",
        "llms_url": f"{origin}/llms.txt" if origin else "/llms.txt",
        "llms_full_url": f"{origin}/llms-full.txt" if origin else "/llms-full.txt",
        "meta_url": f"{origin}/meta.json" if origin else "/meta.json",
        "surface_url": f"{origin}/surface.json" if origin else "/surface.json",
        "semantic_url": f"{origin}/semantic.json" if origin else "/semantic.json",
        "structure_url": f"{origin}/structure.json" if origin else "/structure.json",
        "inventories_url": f"{origin}/inventories.json" if origin else "/inventories.json",
        "objects_inv_url": f"{origin}/objects.inv" if origin else "/objects.inv",
        "page_count": len(catalog.nodes),
        "tools": [
            {
                "name": "search_docs",
                "description": "Search documentation by keyword. Returns ranked pages with snippets.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results (default 12)",
                            "default": 12,
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "get_doc",
                "description": "Retrieve a documentation page by URL path or slug.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Page URL path, e.g. /docs/get-started/installation/",
                        },
                        "slug": {
                            "type": "string",
                            "description": "Catalog slug, e.g. docs/get-started/installation",
                        },
                    },
                },
            },
            {
                "name": "list_docs",
                "description": "List documentation pages, optionally filtered by section.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "section": {
                            "type": "string",
                            "description": "Section id, e.g. get-started or api",
                        },
                        "mount": {
                            "type": "string",
                            "description": "Catalog mount id, e.g. chirp or shared",
                        },
                    },
                },
            },
            {
                "name": "semantic_search",
                "description": "Hybrid keyword + semantic search over documentation chunks.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Natural language query"},
                        "limit": {"type": "integer", "default": 12},
                        "mount": {"type": "string", "description": "Optional mount filter"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "retrieve_doc",
                "description": "Retrieve a node, chunks, backlinks, and similar pages by node_id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "node_id": {
                            "type": "string",
                            "description": "Catalog node id, e.g. chirp:latest:docs/get-started/installation",
                        },
                    },
                    "required": ["node_id"],
                },
            },
        ],
    }


def _absolute_url(base_url: str, path: str) -> str:
    if not base_url:
        return path
    return f"{base_url.rstrip('/')}{path}"
