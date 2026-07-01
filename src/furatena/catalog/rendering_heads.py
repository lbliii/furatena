"""Rendering-head contracts for one catalog powering multiple outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RenderingHeadSpec:
    """Contract a rendering head must honor when consuming catalog nodes."""

    id: str
    label: str
    output: str
    description: str
    required_catalog_fields: tuple[str, ...]
    required_assets: tuple[str, ...] = ()
    navigation: tuple[str, ...] = ()
    unsupported_directives: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


RENDERING_HEADS: tuple[RenderingHeadSpec, ...] = (
    RenderingHeadSpec(
        id="live-shell",
        label="Live persistent shell",
        output="live",
        description="Browser app head with persistent shell, boosted navigation, OOB metadata, search, and author/live runtime affordances.",
        required_catalog_fields=("node_id", "url", "title", "layout", "body_html", "toc"),
        required_assets=("theme-css", "theme-js", "search-index", "catalog-graph"),
        navigation=("persistent-shell", "htmx-boost", "prev-next", "toc", "search"),
        notes=("Mutating author controls require author mode and private-session policy.",),
    ),
    RenderingHeadSpec(
        id="static-document",
        label="Static document",
        output="static",
        description="Standalone HTML document exported from the same catalog graph with sidecar JSON and static-safe enhancement scripts.",
        required_catalog_fields=("node_id", "url", "title", "body_html", "source_path", "provenance"),
        required_assets=("theme-css", "static-search", "catalog-json", "search-json"),
        navigation=("pre-rendered-links", "static-search", "toc"),
        notes=("Interactive API try-it behavior must degrade to static or mock modes.",),
    ),
    RenderingHeadSpec(
        id="embedded-fragment",
        label="Embedded fragment",
        output="embed",
        description="Portable body fragment for host applications that cannot assume Furatena shell chrome or global assets.",
        required_catalog_fields=("node_id", "url", "title", "body_html", "content"),
        required_assets=("scoped-css",),
        navigation=("host-controlled-links",),
        unsupported_directives=("gist", "youtube"),
        notes=("Fragments must not rely on persistent shell state, OOB swaps, or document-level scripts.",),
    ),
    RenderingHeadSpec(
        id="paged-output",
        label="Paged output",
        output="pdf",
        description="Future paged/PDF head driven by catalog text, sections, links, and directive fallbacks.",
        required_catalog_fields=("node_id", "title", "body_text", "sections", "source_path", "provenance"),
        required_assets=("print-css",),
        navigation=("page-breaks", "toc", "resolved-links"),
        unsupported_directives=("gist", "youtube", "iframe"),
        notes=("Media embeds need captions or links before paged export.",),
    ),
)


def rendering_heads_json() -> list[dict[str, Any]]:
    """JSON-serializable rendering-head registry."""
    return [
        {
            "id": head.id,
            "label": head.label,
            "output": head.output,
            "description": head.description,
            "required_catalog_fields": list(head.required_catalog_fields),
            "required_assets": list(head.required_assets),
            "navigation": list(head.navigation),
            "unsupported_directives": list(head.unsupported_directives),
            "notes": list(head.notes),
        }
        for head in RENDERING_HEADS
    ]


def check_rendering_head_contracts(
    catalog: Any,
    *,
    validate_required_fields: bool = False,
) -> tuple[list[str], list[str]]:
    """Check nodes against rendering-head contracts before export."""
    warnings: list[str] = []
    for node in getattr(catalog, "nodes", ()):
        for head in RENDERING_HEADS:
            if validate_required_fields:
                missing = [
                    field
                    for field in head.required_catalog_fields
                    if not _has_required_catalog_field(node, field)
                ]
                if missing:
                    warnings.append(
                        f"{node.source_path or node.slug}: rendering head {head.id} missing catalog field(s): "
                        f"{', '.join(missing)}"
                    )
            for directive in _unsupported_directives(node, head):
                line = f":{directive.line}" if directive.line else ""
                warnings.append(
                    f"{node.source_path or node.slug}{line}: rendering head {head.id} does not support directive "
                    f"{directive.name!r}"
                )
    return [], sorted(warnings)


def _has_required_catalog_field(node: Any, field: str) -> bool:
    if field == "node_id":
        return bool(getattr(node, "node_id", ""))
    if field == "content":
        return getattr(node, "content_ir", None) is not None
    if field == "provenance":
        return bool(getattr(node, "source_path", "")) or bool(getattr(node, "meta", {}).get("source_provider"))
    value = getattr(node, field, None)
    if field == "sections":
        return bool(value) or bool(getattr(node, "body_text", ""))
    return value not in (None, "", ())


def _unsupported_directives(node: Any, head: RenderingHeadSpec):
    content_ir = getattr(node, "content_ir", None)
    directives = getattr(content_ir, "directives", ()) if content_ir is not None else ()
    unsupported = set(head.unsupported_directives)
    return [directive for directive in directives if directive.name in unsupported]
