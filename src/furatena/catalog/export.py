"""Serialize the doc catalog for agents and static export."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from furatena.catalog.access import (
    AccessPermission,
    AccessRole,
    AccessSubject,
    accessible_nodes,
)
from furatena.catalog.content_ir import content_ir_record
from furatena.catalog.graph_schema import graph_node_records
from furatena.catalog.patitas_bridge import excerpt_text, llm_text, plain_text, section_texts
from furatena.catalog.record_types import (
    CatalogGraphRecord,
    EdgeRecord,
    NamespaceRecord,
    PageRecord,
    ProvenanceRecord,
    SearchEntryRecord,
    SearchIndexRecord,
    SearchSectionRecord,
)
from furatena.catalog.search import search_nodes
from furatena.catalog.text import sections_record

if TYPE_CHECKING:
    from furatena.catalog.loader import DocCatalog


class CatalogExport(Protocol):
    active_channel: str
    nodes: tuple[Any, ...]

    def doc_nodes(self) -> list[Any]: ...
    def backlinks_for(self, node: Any) -> list[dict[str, str]]: ...
    def graph_edges(self) -> list[EdgeRecord]: ...
    def namespaces(self) -> list[NamespaceRecord]: ...
    def inventories_metadata(self) -> list[dict[str, Any]]: ...


def _edition_status(catalog: Any, node: Any) -> str:
    status_for = getattr(catalog, "edition_status_for", None)
    if callable(status_for):
        return str(status_for(node.mount, node.edition))
    return "current" if str(getattr(node, "edition", "latest")) == "latest" else "legacy"


def catalog_graph(
    catalog: CatalogExport | DocCatalog,
    *,
    schema_version: int = 3,
    include_private: bool = False,
    subject: AccessSubject | None = None,
) -> CatalogGraphRecord:
    """JSON-serializable view of the documentation graph."""
    pages: list[PageRecord] = []
    nodes = accessible_nodes(
        catalog,
        catalog.nodes,
        subject=subject,
        permission=AccessPermission.EXPORT,
        include_private=include_private,
    )
    node_ids = {node.node_id for node in nodes}
    public_urls = {node.url for node in nodes}
    for node in nodes:
        pages.append(
            _page_record(
                catalog,
                node,
                schema_version=schema_version,
                public_urls=public_urls if not include_private else None,
            )
        )
    edges = [
        edge
        for edge in catalog.graph_edges()
        if edge.get("source") in node_ids
        and (
            edge.get("target") in node_ids
            or _is_external_graph_target(str(edge.get("target") or ""))
        )
    ]
    edition_statuses = {
        (str(page.get("mount") or ""), str(page.get("edition") or "")): str(
            page.get("edition_status") or "current"
        )
        for page in pages
    }
    graph_nodes = graph_node_records(edges, edition_statuses=edition_statuses)
    payload: CatalogGraphRecord = {
        "schema_version": schema_version,
        "version": schema_version,
        "channel": catalog.active_channel,
        "edition": catalog.active_channel,
        "page_count": len(pages),
        "pages": pages,
        "edges": edges,
        "graph_nodes": graph_nodes,
        "namespaces": catalog.namespaces(),
    }
    inventories_method = getattr(catalog, "inventories_metadata", None)
    inventories = inventories_method() if callable(inventories_method) else []
    if inventories:
        payload["inventories"] = inventories
    from furatena.catalog.structure_index import build_structure_index

    structure = build_structure_index(catalog, include_private=include_private)
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
    public_urls: set[str] | None = None,
) -> PageRecord:
    source_kind = node.meta.get("source", "markdown")
    backlinks = catalog.backlinks_for(node)
    if public_urls is not None:
        backlinks = [item for item in backlinks if item.get("href") in public_urls]
    provenance = _provenance_record(catalog, node, source_kind=source_kind)
    record: PageRecord = {
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
        "edition_status": _edition_status(catalog, node),
        "lang": node.lang,
        "translation_key": node.translation_key,
        "section_root": node.section_root,
        "source_provider": provenance["provider"],
        "source_repo": provenance.get("repo"),
        "source_ref": provenance.get("ref"),
        "source_url": provenance.get("source_url"),
        "generated_from": provenance.get("generated_from"),
        "owner": provenance.get("owner"),
        "team": provenance.get("team"),
        "tenant": provenance.get("tenant"),
        "workspace": provenance.get("workspace"),
        "site": provenance.get("site"),
        "output_channel": provenance.get("output_channel"),
        "last_indexed_at": provenance.get("last_indexed_at"),
        "provenance": provenance,
        "api_operation": node.meta.get("api_operation"),
        "api_try_it": node.meta.get("api_try_it"),
        "toc": [
            {"anchor": entry.anchor, "text": entry.text, "depth": entry.depth} for entry in node.toc
        ],
        "backlinks": backlinks,
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


def _is_external_graph_target(target: str) -> bool:
    return target.startswith(
        (
            "api:",
            "api-tag:",
            "auth:",
            "cli:",
            "environment:",
            "example:",
            "inventory:",
            "owner:",
            "ref:",
            "release:",
            "request-body:",
            "response:",
            "schema:",
            "sdk:",
            "source:",
            "tag:",
        )
    )


def _meta_value(meta: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = meta.get(key)
        if value not in (None, ""):
            return value
    return None


def _string_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _last_indexed_at(catalog: CatalogExport | DocCatalog, node) -> str | None:
    existing = _meta_value(node.meta, "last_indexed_at", "indexed_at")
    if existing:
        return str(existing)
    source_mtimes = getattr(catalog, "_source_mtimes", None)
    content_root = getattr(catalog, "content_root", None)
    if not isinstance(source_mtimes, dict) or content_root is None:
        return None
    path = Path(content_root) / node.source_path
    mtime = source_mtimes.get(path)
    if mtime is None:
        return None
    return (
        datetime.fromtimestamp(float(mtime), UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _provenance_record(
    catalog: CatalogExport | DocCatalog,
    node,
    *,
    source_kind: Any,
) -> ProvenanceRecord:
    meta = node.meta
    provider = _string_or_none(_meta_value(meta, "source_provider", "provider")) or (
        "generated" if source_kind != "markdown" else "filesystem"
    )
    owner = _string_or_none(_meta_value(meta, "owner"))
    team = _string_or_none(_meta_value(meta, "team")) or owner
    return {
        "provider": provider,
        "repo": _string_or_none(_meta_value(meta, "source_repo", "repo", "repository")),
        "ref": _string_or_none(_meta_value(meta, "source_ref", "ref", "commit", "branch")),
        "source_url": _string_or_none(_meta_value(meta, "source_url")),
        "path": node.source_path,
        "generated_from": _string_or_none(
            _meta_value(meta, "generated_from", "generated-from", "source_generated_from")
        ),
        "owner": owner,
        "team": team,
        "mount": node.mount,
        "edition": node.edition,
        "edition_status": _edition_status(catalog, node),
        "tenant": _string_or_none(_meta_value(meta, "tenant")),
        "workspace": _string_or_none(_meta_value(meta, "workspace")),
        "site": _string_or_none(_meta_value(meta, "site")),
        "output_channel": getattr(catalog, "active_channel", node.edition),
        "last_indexed_at": _last_indexed_at(catalog, node),
    }


def provenance_record(
    catalog: CatalogExport | DocCatalog,
    node: Any,
) -> ProvenanceRecord:
    """Return the shared provenance shape used by retrieval surfaces."""
    return _provenance_record(
        catalog,
        node,
        source_kind=node.meta.get("source", "markdown"),
    )


def meta_json(
    catalog: CatalogExport | DocCatalog,
    *,
    include_private: bool = False,
    subject: AccessSubject | None = None,
) -> dict[str, Any]:
    """Compact per-page metadata index for agents and static export."""
    pages: list[dict[str, Any]] = []
    nodes = accessible_nodes(
        catalog,
        catalog.nodes,
        subject=subject,
        permission=AccessPermission.EXPORT,
        include_private=include_private,
    )
    for node in nodes:
        source_kind = node.meta.get("source", "markdown")
        provenance = _provenance_record(catalog, node, source_kind=source_kind)
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
                "source_path": node.source_path,
                "source": source_kind,
                "source_kind": "generated" if source_kind != "markdown" else "filesystem",
                "source_provider": provenance["provider"],
                "source_repo": provenance.get("repo"),
                "source_ref": provenance.get("ref"),
                "source_url": provenance.get("source_url"),
                "generated_from": provenance.get("generated_from"),
                "owner": provenance.get("owner"),
                "team": provenance.get("team"),
                "tenant": provenance.get("tenant"),
                "workspace": provenance.get("workspace"),
                "site": provenance.get("site"),
                "mount": node.mount,
                "edition": node.edition,
                "output_channel": provenance.get("output_channel"),
                "last_indexed_at": provenance.get("last_indexed_at"),
                "provenance": provenance,
                "api_operation": node.meta.get("api_operation"),
                "api_try_it": node.meta.get("api_try_it"),
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


def _api_agent_operation(
    catalog: CatalogExport | DocCatalog,
    node: Any,
    *,
    base_url: str = "",
) -> dict[str, Any] | None:
    api_operation = node.meta.get("api_operation")
    if not isinstance(api_operation, dict):
        return None
    source_kind = node.meta.get("source", "markdown")
    provenance = _provenance_record(catalog, node, source_kind=source_kind)
    record: dict[str, Any] = {
        "node_id": node.node_id,
        "url": _absolute_url(base_url, node.url),
        "slug": node.slug,
        "title": node.title,
        "operation_id": api_operation.get("operation_id") or node.meta.get("operation_id"),
        "method": api_operation.get("method"),
        "path": api_operation.get("path"),
        "summary": api_operation.get("summary") or node.description,
        "tags": api_operation.get("tags") or [],
        "schemas": api_operation.get("schemas") or [],
        "request_bodies": api_operation.get("request_bodies") or [],
        "responses": api_operation.get("responses") or [],
        "examples": api_operation.get("examples") or [],
        "auth": api_operation.get("auth") or [],
        "environments": api_operation.get("environments") or [],
        "external_docs": api_operation.get("external_docs") or [],
        "source_spec": api_operation.get("source_spec") or provenance.get("generated_from"),
        "provenance": provenance,
    }
    api_try_it = node.meta.get("api_try_it")
    if isinstance(api_try_it, dict):
        record["try_it"] = {
            "modes": api_try_it.get("modes") or [],
            "static_export": api_try_it.get("static_export") or {},
            "base_urls": api_try_it.get("base_urls") or [],
            "auth": api_try_it.get("auth") or [],
        }
    return record


def _api_agent_operation_groups(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for operation in operations:
        tags = (
            cast(list[Any], operation.get("tags"))
            if isinstance(operation.get("tags"), list)
            else []
        )
        group_names = [str(tag) for tag in tags if tag] or ["untagged"]
        for group_name in group_names:
            group = groups.setdefault(
                group_name,
                {
                    "name": group_name,
                    "operation_count": 0,
                    "operations": [],
                },
            )
            group["operation_count"] += 1
            group["operations"].append(
                {
                    "operation_id": operation.get("operation_id"),
                    "method": operation.get("method"),
                    "path": operation.get("path"),
                    "url": operation.get("url"),
                    "source_spec": operation.get("source_spec"),
                }
            )
    return sorted(groups.values(), key=lambda group: str(group["name"]).lower())


def _api_operation_line(node: Any) -> str | None:
    api_operation = node.meta.get("api_operation")
    if not isinstance(api_operation, dict):
        return None
    method = str(api_operation.get("method") or "").upper()
    path = str(api_operation.get("path") or "")
    operation_id = str(api_operation.get("operation_id") or node.meta.get("operation_id") or "")
    parts = [part for part in (method, path) if part]
    summary = " ".join(parts)
    if operation_id:
        summary = f"{summary} ({operation_id})" if summary else operation_id
    examples = (
        api_operation.get("examples") if isinstance(api_operation.get("examples"), list) else []
    )
    schemas = api_operation.get("schemas") if isinstance(api_operation.get("schemas"), list) else []
    details = []
    if examples:
        details.append(f"examples: {', '.join(str(item) for item in examples)}")
    if schemas:
        details.append(f"schemas: {', '.join(str(item) for item in schemas)}")
    if details:
        summary = f"{summary}; {'; '.join(details)}" if summary else "; ".join(details)
    return summary or None


def api_operations_json(
    catalog: DocCatalog,
    *,
    base_url: str = "",
    include_private: bool = False,
    subject: AccessSubject | None = None,
) -> dict[str, Any]:
    """Agent/SDK-friendly API operation inventory."""
    nodes = accessible_nodes(
        catalog,
        catalog.nodes,
        subject=subject,
        permission=AccessPermission.EXPORT,
        include_private=include_private,
    )
    operations = [
        operation
        for node in nodes
        if (operation := _api_agent_operation(catalog, node, base_url=base_url)) is not None
    ]
    return {
        "schema_version": 1,
        "channel": catalog.active_channel,
        "operation_count": len(operations),
        "groups": _api_agent_operation_groups(operations),
        "operations": operations,
    }


def llms_txt(
    catalog: DocCatalog,
    *,
    site_name: str = "Furatena",
    site_description: str = "",
    include_private: bool = False,
    subject: AccessSubject | None = None,
    mount: str | None = None,
) -> str:
    """Compact llmstxt.org page index with grouped API operation hints."""
    summary = " ".join(site_description.split()) or f"Documentation index for {site_name}."
    active_status = "current"
    lifecycle_for = getattr(catalog, "edition_lifecycle_for", None)
    default_mount = mount or getattr(getattr(catalog, "default_mount", None), "id", "")
    if callable(lifecycle_for) and default_mount:
        active_status = lifecycle_for(default_mount, catalog.active_channel).status
    lines = [
        f"# {site_name} Documentation",
        "",
        f"> {summary}",
        f"> Edition: {catalog.active_channel} ({active_status})",
        "",
    ]
    active_shard = getattr(catalog, "_active_shard", None)
    if mount is not None and callable(active_shard):
        shard = active_shard(mount)
        raw_nodes = shard.doc_nodes() if shard is not None else ()
    else:
        raw_nodes = catalog.doc_nodes()
    nodes = accessible_nodes(
        catalog,
        raw_nodes,
        subject=subject,
        permission=AccessPermission.EXPORT,
        include_private=include_private,
    )
    grouped: dict[str, list[Any]] = {}
    for node in nodes:
        mount = str(getattr(node, "mount", "") or "Documentation")
        section = str(getattr(node, "section", "") or "Overview")
        heading = f"{_llms_label(mount)} — {_llms_label(section)}"
        grouped.setdefault(heading, []).append(node)

    for heading, group_nodes in grouped.items():
        lines.extend((f"## {heading}", ""))
        for node in group_nodes:
            desc = node.description.strip() if node.description else ""
            api_line = _api_operation_line(node)
            url = _markdown_page_alias(node.url)
            if api_line and desc:
                lines.append(f"- [{node.title}]({url}): {desc} API: {api_line}")
            elif api_line:
                lines.append(f"- [{node.title}]({url}): API: {api_line}")
            elif desc:
                lines.append(f"- [{node.title}]({url}): {desc}")
            else:
                lines.append(f"- [{node.title}]({url})")
        lines.append("")
    return "\n".join(lines) + "\n"


def _llms_label(value: str) -> str:
    return value.replace("-", " ").replace("_", " ").strip().title()


def _markdown_page_alias(url: str) -> str:
    path = url.rstrip("/")
    return f"{path}.md" if path else "/index.md"


def surface_json(config: Any | None = None, catalog: Any | None = None) -> dict[str, Any]:
    """Machine-readable view-kind / surface registry."""
    from furatena.catalog.rendering_heads import rendering_heads_json
    from furatena.catalog.view_kinds import VIEW_KINDS

    payload: dict[str, Any] = {
        "schema_version": 1,
        "rendering_heads": rendering_heads_json(),
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
    if config is not None and catalog is not None:
        from furatena.catalog.delivery import delivery_surface_json

        payload["delivery"] = delivery_surface_json(config, catalog)
    return payload


def llms_full_txt(
    catalog: DocCatalog,
    *,
    site_name: str = "Furatena",
    include_private: bool = False,
    subject: AccessSubject | None = None,
) -> str:
    """Full LLM-safe corpus for agents (Patitas ``render_llm`` when AST is available)."""
    active_status = "current"
    lifecycle_for = getattr(catalog, "edition_lifecycle_for", None)
    default_mount = getattr(getattr(catalog, "default_mount", None), "id", "")
    if callable(lifecycle_for) and default_mount:
        active_status = lifecycle_for(default_mount, catalog.active_channel).status
    lines = [
        f"# {site_name} Documentation (full corpus)",
        "",
        f"> Edition: {catalog.active_channel} ({active_status})",
        "",
    ]
    documents = (
        catalog.ast_documents()
        if hasattr(catalog, "ast_documents")
        else getattr(catalog, "_ast_documents", None)
    )
    nodes = accessible_nodes(
        catalog,
        catalog.doc_nodes(),
        subject=subject,
        permission=AccessPermission.EXPORT,
        include_private=include_private,
    )
    for node in nodes:
        lines.extend((f"## {node.title}", ""))
        lines.extend((f"Edition status: {_edition_status(catalog, node)}", ""))
        if node.description.strip():
            lines.extend((node.description.strip(), ""))
        api_line = _api_operation_line(node)
        if api_line:
            lines.extend((f"API operation: {api_line}", ""))
        document = None
        if isinstance(documents, dict):
            document = documents.get(node.node_id) or documents.get(node.slug)
        body = llm_text(node, document, source=node.body_md)
        if body:
            lines.extend((body, ""))
        lines.extend(("---", ""))
    return "\n".join(lines).rstrip() + "\n"


def search_json(
    catalog: DocCatalog,
    *,
    base_url: str = "",
    include_private: bool = False,
    subject: AccessSubject | None = None,
    status: str | None = None,
    include_preview: bool = False,
    include_eol: bool = False,
) -> SearchIndexRecord:
    """Machine-readable search index for tools and agents."""
    entries: list[SearchEntryRecord] = []
    sections: set[str] = set()
    tags: set[str] = set()
    languages: set[str] = set()

    nodes = catalog.all_doc_nodes() if hasattr(catalog, "all_doc_nodes") else catalog.doc_nodes()
    nodes = accessible_nodes(
        catalog,
        nodes,
        subject=subject,
        permission=AccessPermission.SEARCH,
        include_private=include_private,
    )
    from furatena.catalog.edition_lifecycle import lifecycle_statuses

    selected_statuses = lifecycle_statuses(
        status=status, include_preview=include_preview, include_eol=include_eol
    )
    nodes = [node for node in nodes if _edition_status(catalog, node) in selected_statuses]
    documents = (
        catalog.ast_documents()
        if hasattr(catalog, "ast_documents")
        else getattr(catalog, "_ast_documents", None)
    )
    for node in nodes:
        sections.add(node.section)
        tags.update(node.tags)
        languages.add(getattr(node, "lang", "en"))
        document = None
        if isinstance(documents, dict):
            document = documents.get(node.node_id) or documents.get(node.slug)
        body_text = (
            node.body_text.strip()
            or plain_text(node, document, source=node.body_md)
            or node.description
        )
        blocks: list[SearchSectionRecord] = []
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
        entry: SearchEntryRecord = {
            "node_id": node.node_id,
            "url": _absolute_url(base_url, node.url),
            "title": node.title,
            "description": node.description,
            "section": node.section,
            "snippet": body_text[:240],
            "mount": node.mount,
            "edition": node.edition,
            "edition_status": _edition_status(catalog, node),
            "tags": sorted(node.tags),
            "lang": getattr(node, "lang", "en"),
            "provenance": provenance_record(catalog, node),
        }
        if getattr(node, "translation_key", None):
            entry["translation_key"] = node.translation_key
        api_agent_operation = _api_agent_operation(catalog, node, base_url=base_url)
        if api_agent_operation is not None:
            entry["api_operation"] = api_agent_operation
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
    include_private: bool = False,
    subject: AccessSubject | None = None,
    status: str | None = None,
    include_preview: bool = False,
    include_eol: bool = False,
) -> dict[str, Any]:
    """Ranked search results in the same schema as ``search_json`` entries."""
    hits = search_nodes(
        accessible_nodes(
            catalog,
            catalog.doc_nodes(),
            subject=subject,
            permission=AccessPermission.SEARCH,
            include_private=include_private,
        ),
        query,
        limit=limit,
        documents=catalog.ast_documents()
        if hasattr(catalog, "ast_documents")
        else getattr(catalog, "_ast_documents", None),
        status_for=lambda node: _edition_status(catalog, node),
        status=status,
        include_preview=include_preview,
        include_eol=include_eol,
    )
    return {
        "version": 1,
        "query": query,
        "count": len(hits),
        "results": [
            {
                "node_id": hit.node.node_id,
                "url": _absolute_url(base_url, hit.node.url),
                "title": hit.node.title,
                "description": hit.node.description,
                "section": hit.node.section,
                "snippet": hit.snippet,
                "score": hit.score,
                "mount": hit.node.mount,
                "edition": hit.node.edition,
                "edition_status": _edition_status(catalog, hit.node),
                "tags": sorted(hit.node.tags),
                "provenance": provenance_record(catalog, hit.node),
                **(
                    {"api_operation": api_operation}
                    if (
                        api_operation := _api_agent_operation(
                            catalog,
                            hit.node,
                            base_url=base_url,
                        )
                    )
                    is not None
                    else {}
                ),
            }
            for hit in hits
        ],
    }


def tools_manifest(
    catalog: DocCatalog,
    *,
    base_url: str = "",
    site_name: str = "Furatena",
    include_private: bool = False,
    subject: AccessSubject | None = None,
) -> dict[str, Any]:
    """Stable MCP-style tool schema over the documentation catalog."""
    origin = base_url.rstrip("/")
    tool_slug = "-".join(part for part in site_name.lower().split() if part) or "furatena"
    nodes = accessible_nodes(
        catalog,
        catalog.nodes,
        subject=subject,
        permission=AccessPermission.EXPORT,
        include_private=include_private,
    )
    api_operations = [
        operation
        for node in nodes
        if (operation := _api_agent_operation(catalog, node, base_url=base_url)) is not None
    ]
    trusted_subject = bool(
        subject is not None and subject.roles != frozenset({AccessRole.ANONYMOUS})
    )
    payload: dict[str, Any] = {
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
        "channels_url": f"{origin}/channels.json" if origin else "/channels.json",
        "deployment_profiles_url": (
            f"{origin}/deployment-profiles.json" if origin else "/deployment-profiles.json"
        ),
        "api_operations_url": (
            f"{origin}/catalog/api-operations.json" if origin else "/catalog/api-operations.json"
        ),
        "page_count": len(nodes),
        "access": {
            "visibility": "trusted" if include_private or trusted_subject else "public",
            "include_private": include_private or trusted_subject,
        },
        "api_operation_count": len(api_operations),
        "api_operation_groups": _api_agent_operation_groups(api_operations),
        "api_operations": api_operations,
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
            {
                "name": "list_api_operations",
                "description": "List API operations with method, path, schema, example, auth, and source-spec metadata.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "tag": {
                            "type": "string",
                            "description": "Optional API tag/group filter.",
                        },
                        "operation_id": {
                            "type": "string",
                            "description": "Optional OpenAPI operationId filter.",
                        },
                    },
                },
            },
        ],
    }
    inventory_store = getattr(catalog, "inventory_store", None)
    if inventory_store is not None and inventory_store.specs:
        payload["inventories_url"] = f"{origin}/inventories.json" if origin else "/inventories.json"
        payload["objects_inv_url"] = f"{origin}/objects.inv" if origin else "/objects.inv"
    return payload


def _absolute_url(base_url: str, path: str) -> str:
    if not base_url:
        return path
    return f"{base_url.rstrip('/')}{path}"
