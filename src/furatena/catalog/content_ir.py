"""Extract structured content IR from Patitas document AST."""

from __future__ import annotations

import html
import re
from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any

from patitas.nodes import Directive, Document, FencedCode, Heading, Link, Node, Text
from patitas.visitor import BaseVisitor

from furatena.catalog.models import (
    ContentDirective,
    ContentHeading,
    ContentIR,
    ContentLink,
    TocEntry,
)

if TYPE_CHECKING:
    from furatena.catalog.models import DocNode

_SLUGIFY_RE = re.compile(r"[^\w\s-]")
_WHITESPACE_RE = re.compile(r"[\s_]+")


def slugify_heading(text: str) -> str:
    slug = _SLUGIFY_RE.sub("", text.lower())
    return _WHITESPACE_RE.sub("-", slug).strip("-")


def _node_line(node: Node) -> int | None:
    location = getattr(node, "location", None)
    if location is None:
        return None
    return getattr(location, "lineno", None)


def _inline_text(node: Node) -> str:
    parts: list[str] = []

    def walk(current: Node) -> None:
        if isinstance(current, Text):
            value = current.content
            if value:
                parts.append(value)
            return
        for child in getattr(current, "children", ()) or ():
            walk(child)

    walk(node)
    return "".join(parts).strip()


def _directive_options(node: Directive) -> dict[str, Any]:
    options = node.options
    if options is None:
        return {}
    if is_dataclass(options):
        raw = asdict(options)
        return {key: value for key, value in raw.items() if value not in (None, "", 0, False, ())}
    return {}


class _ContentIRVisitor(BaseVisitor[None]):
    """Collect headings, links, and directives via Patitas visitor dispatch."""

    def __init__(self) -> None:
        self.headings: list[ContentHeading] = []
        self.links: list[ContentLink] = []
        self.directives: list[ContentDirective] = []
        self.features: set[str] = set()

    def visit_heading(self, node: Heading) -> None:
        text = _inline_text(node)
        anchor = node.explicit_id or slugify_heading(text)
        self.headings.append(
            ContentHeading(
                level=int(node.level),
                text=text,
                anchor=anchor,
                line=_node_line(node),
            )
        )

    def visit_link(self, node: Link) -> None:
        self.links.append(
            ContentLink(
                href=str(node.url or ""),
                text=_inline_text(node) or str(node.title or ""),
                line=_node_line(node),
            )
        )

    def visit_directive(self, node: Directive) -> None:
        self.directives.append(
            ContentDirective(
                name=str(node.name or ""),
                options=_directive_options(node),
                line=_node_line(node),
            )
        )

    def visit_fenced_code(self, node: FencedCode) -> None:
        info = html.unescape(node.info) if node.info else ""
        language = info.split()[0].lower() if info else ""
        if language == "mermaid":
            self.features.add("mermaid")


def extract_content_ir(document: Document) -> ContentIR:
    """Walk a Patitas document and collect headings, links, and directives."""
    visitor = _ContentIRVisitor()
    visitor.visit(document)
    return ContentIR(
        headings=tuple(visitor.headings),
        links=tuple(visitor.links),
        directives=tuple(visitor.directives),
        features=frozenset(visitor.features),
    )


def content_ir_to_toc(content_ir: ContentIR) -> tuple[TocEntry, ...]:
    """Map Content IR headings to catalog TOC entries."""
    return tuple(
        TocEntry(anchor=heading.anchor, text=heading.text, depth=heading.level)
        for heading in content_ir.headings
    )


def content_ir_record(
    content_ir: ContentIR | None, *, schema_version: int = 2
) -> dict[str, Any] | None:
    """JSON-serializable summary for catalog export."""
    if content_ir is None:
        return None
    extensions = [
        {
            "name": directive.name,
            "options": directive.options,
            "line": directive.line,
        }
        for directive in content_ir.directives
    ]
    record: dict[str, Any] = {
        "headings": [
            {
                "level": heading.level,
                "text": heading.text,
                "anchor": heading.anchor,
                "line": heading.line,
            }
            for heading in content_ir.headings
        ],
        "links": [
            {
                "href": link.href,
                "text": link.text,
                "line": link.line,
                **({"mount": link.mount} if schema_version >= 3 and link.mount else {}),
                **({"domain": link.domain} if schema_version >= 3 and link.domain else {}),
                **(
                    {"inventory_id": link.inventory_id}
                    if schema_version >= 3 and link.inventory_id
                    else {}
                ),
                **(
                    {"resolved": link.resolved} if schema_version >= 3 and not link.resolved else {}
                ),
            }
            for link in content_ir.links
        ],
    }
    if schema_version >= 3:
        record["extensions"] = extensions
    record["directives"] = extensions
    return record


def collect_content_ir_urls(content_ir: ContentIR) -> set[str]:
    """Collect internal page URLs from markdown links and directive options."""
    from furatena.catalog.graph import normalize_internal_url

    urls: set[str] = set()
    for link in content_ir.links:
        normalized = normalize_internal_url(link.href)
        if normalized is not None:
            urls.add(normalized)
    for directive in content_ir.directives:
        urls.update(_directive_option_urls(directive))
    return urls


_LINK_OPTION_KEYS = frozenset({"link", "href", "url", "target", "to"})


def _directive_option_urls(directive: ContentDirective) -> set[str]:
    from furatena.catalog.graph import normalize_internal_url

    urls: set[str] = set()
    options = directive.options or {}
    if directive.name == "card":
        link = options.get("link")
        if link:
            normalized = normalize_internal_url(str(link))
            if normalized is not None:
                urls.add(normalized)
    for key, value in options.items():
        if key not in _LINK_OPTION_KEYS:
            continue
        if not value:
            continue
        normalized = normalize_internal_url(str(value))
        if normalized is not None:
            urls.add(normalized)
    return urls


def collect_node_link_urls(
    node,
    catalog=None,
) -> set[str]:
    """Collect internal URLs for a page from Content IR and catalog-aware directives."""

    urls: set[str] = set()
    content_ir = node.content_ir
    if content_ir is None and node.body_md:
        from furatena.catalog.render import DocsRenderer

        _document, content_ir = DocsRenderer().parse(node.body_md)
    if content_ir is not None:
        urls.update(collect_content_ir_urls(content_ir))
        if catalog is not None:
            for directive in content_ir.directives:
                if directive.name == "child-cards":
                    urls.update(_child_card_urls(node, catalog))
                elif directive.name == "related":
                    urls.update(_related_directive_urls(node, catalog))
    return urls


def _child_card_urls(node: DocNode, catalog) -> set[str]:
    urls: set[str] = set()
    prefix = node.slug.rstrip("/") + "/"
    parent_depth = node.slug.count("/")
    nodes = catalog.nodes if hasattr(catalog, "nodes") else ()
    for child in nodes:
        if child.mount != node.mount or child.edition != node.edition:
            continue
        if not child.slug.startswith(prefix):
            continue
        if child.slug.count("/") != parent_depth + 1:
            continue
        urls.add(child.url)
    return urls


def _related_directive_urls(node: DocNode, catalog) -> set[str]:
    from furatena.catalog.graph import normalize_internal_url

    urls: set[str] = set()
    if not hasattr(catalog, "backlinks_for"):
        return urls
    for ref in catalog.backlinks_for(node):
        href = ref.get("href")
        if not href:
            continue
        normalized = normalize_internal_url(str(href))
        if normalized is not None:
            urls.add(normalized)
    return urls


def content_ir_from_record(raw: dict[str, Any] | None) -> ContentIR | None:
    """Restore Content IR from a frozen catalog page record."""
    if not raw or not isinstance(raw, dict):
        return None

    headings = tuple(
        ContentHeading(
            level=int(item["level"]),
            text=str(item["text"]),
            anchor=str(item["anchor"]),
            line=item.get("line"),
        )
        for item in raw.get("headings") or []
        if isinstance(item, dict) and "anchor" in item
    )
    links = tuple(
        ContentLink(
            href=str(item["href"]),
            text=str(item.get("text") or ""),
            line=item.get("line"),
        )
        for item in raw.get("links") or []
        if isinstance(item, dict) and item.get("href")
    )
    directives = tuple(
        ContentDirective(
            name=str(item["name"]),
            options=dict(item.get("options") or {}),
            line=item.get("line"),
        )
        for item in (raw.get("directives") or raw.get("extensions") or [])
        if isinstance(item, dict) and item.get("name")
    )
    return ContentIR(headings=headings, links=links, directives=directives)
