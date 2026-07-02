"""PDF export for catalog pages, collections, and site bundles."""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer

from furatena.catalog.access import AccessPermission, accessible_nodes
from furatena.catalog.channel_manifest import channel_manifest


@dataclass(frozen=True, slots=True)
class PDFExportOptions:
    """Inputs for one PDF export run."""

    output_dir: Path
    page: str | None = None
    collection: str | None = None
    site_name: str = "Furatena"
    base_url: str = ""
    update_channel_manifest: bool = True


@dataclass(frozen=True, slots=True)
class PDFExportResult:
    """Summary of generated PDF artifacts."""

    output_dir: Path
    target: str
    paths: tuple[Path, ...]
    page_count: int
    byte_count: int


def export_pdfs(catalog: Any, *, config: Any | None = None, options: PDFExportOptions) -> PDFExportResult:
    """Render the selected catalog scope to one PDF artifact."""
    output_dir = options.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    target, nodes = _selected_nodes(catalog, page=options.page, collection=options.collection)
    if not nodes:
        raise ValueError("no public pages matched the PDF export target")
    filename = _filename_for_target(target, options.page or options.collection)
    path = output_dir / filename
    _write_pdf(path, nodes, site_name=options.site_name, target=target)
    _write_pdf_manifest(output_dir, target=target, paths=(path,), nodes=nodes)
    if options.update_channel_manifest:
        _update_public_channel_manifest(
            catalog,
            config=config,
            output_dir=output_dir,
            paths=(path,),
            base_url=options.base_url,
        )
    return PDFExportResult(
        output_dir=output_dir,
        target=target,
        paths=(path,),
        page_count=len(nodes),
        byte_count=path.stat().st_size,
    )


def _selected_nodes(
    catalog: Any,
    *,
    page: str | None,
    collection: str | None,
) -> tuple[str, list[Any]]:
    nodes = accessible_nodes(
        catalog,
        catalog.doc_nodes(),
        permission=AccessPermission.EXPORT,
    )
    if page:
        node = _resolve_page(catalog, page)
        if node is None or node.node_id not in {item.node_id for item in nodes}:
            raise ValueError(f"unknown or non-public page: {page}")
        return "page", [node]
    if collection:
        key = collection.strip().strip("/")
        selected = [
            node
            for node in nodes
            if node.section == key
            or node.mount == key
            or node.slug == key
            or node.slug.startswith(f"{key}/")
            or node.url.strip("/").startswith(f"{key}/")
        ]
        if not selected:
            raise ValueError(f"unknown or empty public collection: {collection}")
        return "collection", sorted(selected, key=lambda item: (item.mount, item.section, item.weight, item.title))
    return "site", sorted(nodes, key=lambda item: (item.mount, item.section, item.weight, item.title))


def _resolve_page(catalog: Any, target: str) -> Any | None:
    text = target.strip()
    if not text:
        return None
    if text.startswith("/") and hasattr(catalog, "get_path"):
        return catalog.get_path(text)
    slug = text.strip("/")
    if hasattr(catalog, "get_by_slug"):
        node = catalog.get_by_slug(slug)
        if node is not None:
            return node
    if hasattr(catalog, "get_path"):
        return catalog.get_path(f"/{slug}/")
    return None


def _write_pdf(path: Path, nodes: list[Any], *, site_name: str, target: str) -> None:
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="FuraTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            spaceAfter=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="FuraH2",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            spaceBefore=10,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="FuraMeta",
            parent=styles["Normal"],
            textColor=colors.HexColor("#475569"),
            fontSize=8,
            leading=10,
            spaceAfter=8,
        )
    )
    story: list[Any] = [
        Paragraph(escape(site_name), styles["FuraTitle"]),
        Paragraph(f"PDF export: {escape(target)}", styles["FuraMeta"]),
        Spacer(1, 0.1 * inch),
    ]
    for index, node in enumerate(nodes):
        if index:
            story.append(PageBreak())
        story.extend(_node_story(node, styles))
    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        rightMargin=0.7 * inch,
        leftMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title=f"{site_name} PDF export",
        author="Furatena",
    )
    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)


def _node_story(node: Any, styles: dict[str, Any]) -> list[Any]:
    items: list[Any] = [
        Paragraph(escape(node.title), styles["Heading1"]),
        Paragraph(escape(node.url), styles["FuraMeta"]),
    ]
    if node.description:
        items.append(Paragraph(escape(node.description), styles["BodyText"]))
        items.append(Spacer(1, 0.08 * inch))
    if getattr(node, "sections", ()):
        for section in node.sections:
            if section.heading:
                items.append(Paragraph(escape(section.heading), styles["FuraH2"]))
            if section.text:
                items.extend(_paragraphs(section.text, styles))
    else:
        body = getattr(node, "body_text", "") or _markdown_without_code(getattr(node, "body_md", ""))
        items.extend(_paragraphs(body, styles))
    code_blocks = _code_blocks(getattr(node, "body_md", ""))
    for code in code_blocks:
        items.append(Paragraph("Code", styles["FuraH2"]))
        items.append(Preformatted(_wrap_code(code), styles["Code"]))
    links = _links(node)
    if links:
        items.append(Paragraph("Links", styles["FuraH2"]))
        for label, href in links:
            items.append(Paragraph(f"{escape(label)} - {escape(href)}", styles["BodyText"]))
    return items


def _paragraphs(text: str, styles: dict[str, Any]) -> list[Any]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text or "") if block.strip()]
    return [
        Paragraph(escape(_collapse(block)), styles["BodyText"])
        for block in blocks[:24]
        if _collapse(block)
    ]


def _links(node: Any) -> list[tuple[str, str]]:
    content_ir = getattr(node, "content_ir", None)
    links = getattr(content_ir, "links", ()) if content_ir is not None else ()
    records: list[tuple[str, str]] = []
    for link in links:
        href = str(getattr(link, "href", "") or "")
        label = str(getattr(link, "text", "") or href)
        if href:
            records.append((label, href))
    return records[:20]


def _code_blocks(markdown: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    in_code = False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                blocks.append("\n".join(current).strip())
                current = []
            in_code = not in_code
            continue
        if in_code:
            current.append(line)
    return [block for block in blocks if block][:8]


def _markdown_without_code(markdown: str) -> str:
    lines: list[str] = []
    in_code = False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            lines.append(line)
    return "\n".join(lines)


def _wrap_code(code: str) -> str:
    wrapped: list[str] = []
    for line in code.splitlines():
        wrapped.extend(textwrap.wrap(line, width=92, replace_whitespace=False) or [""])
    return "\n".join(wrapped)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _page_footer(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawRightString(7.8 * inch, 0.42 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _filename_for_target(target: str, raw: str | None) -> str:
    if target == "site":
        return "site.pdf"
    return f"{target}-{_safe_name(raw or target)}.pdf"


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().strip("/").lower())
    return text.strip(".-_") or "export"


def _write_pdf_manifest(output_dir: Path, *, target: str, paths: tuple[Path, ...], nodes: list[Any]) -> None:
    payload = {
        "schema_version": 1,
        "target": target,
        "page_count": len(nodes),
        "artifacts": [
            {
                "path": path.name,
                "bytes": path.stat().st_size,
                "media_type": "application/pdf",
            }
            for path in paths
        ],
        "nodes": [
            {
                "node_id": node.node_id,
                "title": node.title,
                "url": node.url,
                "mount": node.mount,
                "section": node.section,
            }
            for node in nodes
        ],
    }
    (output_dir / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _update_public_channel_manifest(
    catalog: Any,
    *,
    config: Any | None,
    output_dir: Path,
    paths: tuple[Path, ...],
    base_url: str,
) -> None:
    public_root = output_dir.parent if output_dir.name == "pdf" else output_dir
    export_manifest = public_root / "export.manifest.json"
    artifact_paths: list[str] = []
    fingerprints: dict[str, str] = {}
    if export_manifest.is_file():
        try:
            existing = json.loads(export_manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
        artifact_paths = [str(path) for path in existing.get("paths") or []]
        fingerprints = {
            str(key): str(value)
            for key, value in (existing.get("fingerprints") or {}).items()
        }
    pdf_paths = [
        path.relative_to(public_root).as_posix() if path.is_relative_to(public_root) else path.name
        for path in paths
    ]
    for rel in pdf_paths:
        if rel not in artifact_paths:
            artifact_paths.append(rel)
    payload = channel_manifest(
        catalog,
        config=config,
        base_url=base_url,
        mode="static" if export_manifest.is_file() else "pdf",
        paths=sorted(artifact_paths),
        pdf_paths=pdf_paths,
        fingerprints=fingerprints,
    )
    (public_root / "channels.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
