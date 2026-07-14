"""PDF export for catalog pages, collections, and site bundles."""

from __future__ import annotations

import hashlib
import re
import textwrap
from collections import defaultdict
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from patitas.nodes import (
    BlockQuote,
    CodeSpan,
    Directive,
    Emphasis,
    FencedCode,
    Heading,
    HtmlBlock,
    Image,
    IndentedCode,
    LineBreak,
    Link,
    List,
    MathBlock,
    SoftBreak,
    Strikethrough,
    Strong,
    Text,
    ThematicBreak,
)
from patitas.nodes import (
    Paragraph as ASTParagraph,
)
from patitas.nodes import (
    Table as ASTTable,
)
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from furatena.catalog.access import AccessPermission, accessible_nodes
from furatena.catalog.channel_manifest import channel_manifest
from furatena.catalog.deployment_manifest import (
    DeploymentArtifact,
    DeploymentManifest,
    read_deployment_manifest,
    write_deployment_manifest,
)
from furatena.catalog.patitas_bridge import document_for_node
from furatena.catalog.render import DocsRenderer


@dataclass(frozen=True, slots=True)
class PDFExportOptions:
    """Inputs for one PDF export run."""

    output_dir: Path
    page: str | None = None
    collection: str | None = None
    site_name: str = "Furatena"
    base_url: str = ""
    paper: str = "letter"
    grayscale: bool = False
    update_channel_manifest: bool = True


@dataclass(frozen=True, slots=True)
class PDFExportResult:
    """Summary of generated PDF artifacts."""

    output_dir: Path
    target: str
    paths: tuple[Path, ...]
    page_count: int
    node_count: int
    byte_count: int


def export_pdfs(
    catalog: Any, *, config: Any | None = None, options: PDFExportOptions
) -> PDFExportResult:
    """Render the selected catalog scope to one PDF artifact."""
    output_dir = options.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    target, nodes = _selected_nodes(catalog, page=options.page, collection=options.collection)
    if not nodes:
        raise ValueError("no public pages matched the PDF export target")
    filename = _filename_for_target(target, options.page or options.collection)
    path = output_dir / filename
    _write_pdf(
        path,
        nodes,
        site_name=options.site_name,
        target=target,
        base_url=options.base_url,
        paper=options.paper,
        grayscale=options.grayscale,
    )
    physical_page_count = len(PdfReader(str(path)).pages)
    _write_pdf_manifest(
        output_dir,
        target=target,
        paths=(path,),
        nodes=nodes,
        page_count=physical_page_count,
        paper=options.paper,
        grayscale=options.grayscale,
        base_url=options.base_url,
    )
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
        page_count=physical_page_count,
        node_count=len(nodes),
        byte_count=path.stat().st_size,
    )


def _selected_nodes(
    catalog: Any,
    *,
    page: str | None,
    collection: str | None,
) -> tuple[str, list[Any]]:
    catalog_nodes = list(getattr(catalog, "nodes", ()) or catalog.doc_nodes())
    nodes = accessible_nodes(catalog, catalog_nodes, permission=AccessPermission.EXPORT)
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
        return "collection", sorted(
            selected, key=lambda item: (item.mount, item.section, item.weight, item.title)
        )
    return "site", sorted(
        nodes, key=lambda item: (item.mount, item.section, item.weight, item.title)
    )


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


class _SemanticDocTemplate(SimpleDocTemplate):
    """ReportLab template that emits a deterministic heading outline."""

    def afterFlowable(self, flowable: Flowable) -> None:
        outline = getattr(flowable, "outline", None)
        if not outline:
            return
        key, title, level = outline
        previous = int(getattr(self, "_last_outline_level", -1))
        level = 0 if level <= 0 else min(level, previous + 1)
        self._last_outline_level = level
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(title, key, level=max(0, level), closed=False)


class _TaggedFlowable(Flowable):
    """Wrap one flowable in a marked-content span recorded for post-processing."""

    def __init__(
        self,
        inner: Flowable,
        structure_type: str,
        records: list[dict[str, Any]],
        *,
        outline: tuple[str, str, int] | None = None,
    ) -> None:
        super().__init__()
        self.inner = inner
        self.structure_type = structure_type
        self.records = records
        self.outline = outline

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        self.inner.canv = self.canv
        self.width, self.height = self.inner.wrap(availWidth, availHeight)
        return self.width, self.height

    def split(self, availWidth: float, availheight: float) -> list[Flowable]:
        self.inner.canv = self.canv
        pieces = self.inner.split(availWidth, availheight)
        return [
            _TaggedFlowable(
                piece,
                self.structure_type,
                self.records,
                outline=self.outline if index == 0 else None,
            )
            for index, piece in enumerate(pieces)
        ]

    def drawOn(self, canvas: Any, x: float, y: float, _sW: float = 0) -> None:
        page_index = int(canvas.getPageNumber()) - 1
        mcid = sum(1 for item in self.records if item["page"] == page_index)
        canvas.addLiteral(f"/{self.structure_type} <</MCID {mcid}>> BDC")
        self.inner.drawOn(canvas, x, y, _sW)
        canvas.addLiteral("EMC")
        self.records.append({"page": page_index, "mcid": mcid, "type": self.structure_type})


class _TagMarker(Flowable):
    """Zero-height marked-content anchor for a splittable composite flowable."""

    def __init__(self, structure_type: str, records: list[dict[str, Any]]) -> None:
        super().__init__()
        self.structure_type = structure_type
        self.records = records

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        return 0, 0

    def draw(self) -> None:
        page_index = int(self.canv.getPageNumber()) - 1
        mcid = sum(1 for item in self.records if item["page"] == page_index)
        self.canv.addLiteral(f"/{self.structure_type} <</MCID {mcid}>> BDC EMC")
        self.records.append({"page": page_index, "mcid": mcid, "type": self.structure_type})


def _write_pdf(
    path: Path,
    nodes: list[Any],
    *,
    site_name: str,
    target: str,
    base_url: str,
    paper: str,
    grayscale: bool,
) -> None:
    paper_key = paper.strip().lower()
    if paper_key not in {"letter", "a4"}:
        raise ValueError(f"unsupported PDF paper size: {paper}")
    pagesize = LETTER if paper_key == "letter" else A4
    meta_color = colors.HexColor("#4b4b4b" if grayscale else "#475569")
    code_surface = colors.HexColor("#f2f2f2" if grayscale else "#f1f5f9")
    callout_border = colors.HexColor("#686868" if grayscale else "#64748b")
    table_header = colors.HexColor("#e5e5e5" if grayscale else "#e2e8f0")
    table_grid = colors.HexColor("#999999" if grayscale else "#94a3b8")
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
            name="FuraMeta",
            parent=styles["Normal"],
            textColor=meta_color,
            fontSize=8,
            leading=10,
            spaceAfter=8,
        )
    )
    for level, size in ((1, 18), (2, 15), (3, 13), (4, 11), (5, 10), (6, 9)):
        styles.add(
            ParagraphStyle(
                name=f"FuraH{level}",
                parent=styles["Heading1" if level == 1 else "Heading2"],
                fontName="Helvetica-Bold",
                fontSize=size,
                leading=size + 3,
                spaceBefore=10 if level > 1 else 6,
                spaceAfter=5,
                keepWithNext=True,
            )
        )
    styles.add(
        ParagraphStyle(
            name="FuraBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="FuraCode",
            parent=styles["Code"],
            fontName="Courier",
            fontSize=7.2,
            leading=9.2,
            leftIndent=7,
            rightIndent=7,
            spaceBefore=4,
            spaceAfter=7,
            backColor=code_surface,
        )
    )
    styles.add(
        ParagraphStyle(
            name="FuraCallout",
            parent=styles["FuraBody"],
            leftIndent=12,
            textColor=callout_border,
            borderWidth=0,
            backColor=None,
            spaceBefore=5,
            spaceAfter=5,
        )
    )
    tag_records: list[dict[str, Any]] = []
    story: list[Any] = [
        _tag(Paragraph(escape(site_name), styles["FuraTitle"]), "H1", tag_records),
        _tag(Paragraph(f"PDF export: {escape(target)}", styles["FuraMeta"]), "P", tag_records),
        Spacer(1, 0.1 * inch),
    ]
    for index, node in enumerate(nodes):
        if index:
            story.append(PageBreak())
        story.extend(
            _node_story(
                node,
                styles,
                tag_records=tag_records,
                base_url=base_url,
                node_index=index,
                table_header=table_header,
                table_grid=table_grid,
            )
        )
    title = _document_title(nodes, site_name=site_name, target=target)
    subject = _document_subject(nodes, base_url=base_url, target=target)
    doc = _SemanticDocTemplate(
        str(path),
        pagesize=pagesize,
        rightMargin=0.7 * inch,
        leftMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title=title,
        author="Furatena",
        subject=subject,
        keywords="documentation, publication, PDF",
    )

    def footer(canvas: Any, template: Any) -> None:
        _page_footer(canvas, template, pagesize=pagesize)

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    _apply_semantic_tags(path, tag_records, lang=_document_language(nodes))


def _node_story(
    node: Any,
    styles: dict[str, Any],
    *,
    tag_records: list[dict[str, Any]],
    base_url: str,
    node_index: int,
    table_header: Any,
    table_grid: Any,
) -> list[Any]:
    canonical = _canonical_url(base_url, node.url)
    outline = (f"node-{node_index}", str(node.title), 0)
    items: list[Any] = [
        _tag(
            Paragraph(escape(node.title), styles["FuraH1"]),
            "H1",
            tag_records,
            outline=outline,
        ),
        _tag(
            Paragraph(
                f'<link href="{escape(canonical, quote=True)}">{escape(canonical)}</link>',
                styles["FuraMeta"],
            ),
            "P",
            tag_records,
        ),
    ]
    if node.description:
        items.append(
            _tag(Paragraph(escape(node.description), styles["FuraBody"]), "P", tag_records)
        )
        items.append(Spacer(1, 0.08 * inch))
    document = document_for_node(node)
    if document is None and getattr(node, "body_md", ""):
        document, _content_ir = DocsRenderer().parse(node.body_md)
    if document is not None:
        for child_index, child in enumerate(document.children):
            items.extend(
                _block_flowables(
                    child,
                    source=node.body_md,
                    styles=styles,
                    tag_records=tag_records,
                    base_url=base_url,
                    outline_prefix=f"node-{node_index}-{child_index}",
                    table_header=table_header,
                    table_grid=table_grid,
                )
            )
    else:
        body = getattr(node, "body_text", "") or getattr(node, "body_md", "")
        items.extend(_paragraphs(body, styles, tag_records=tag_records))
    return items


def _paragraphs(
    text: str, styles: dict[str, Any], *, tag_records: list[dict[str, Any]]
) -> list[Any]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text or "") if block.strip()]
    return [
        _tag(Paragraph(escape(_collapse(block)), styles["FuraBody"]), "P", tag_records)
        for block in blocks
        if _collapse(block)
    ]


def _tag(
    flowable: Flowable,
    structure_type: str,
    records: list[dict[str, Any]],
    *,
    outline: tuple[str, str, int] | None = None,
) -> _TaggedFlowable:
    return _TaggedFlowable(flowable, structure_type, records, outline=outline)


def _block_flowables(
    node: Any,
    *,
    source: str,
    styles: dict[str, Any],
    tag_records: list[dict[str, Any]],
    base_url: str,
    outline_prefix: str,
    table_header: Any,
    table_grid: Any,
) -> list[Flowable]:
    if isinstance(node, Heading):
        level = max(1, min(6, int(node.level)))
        title = _inline_plain(node.children)
        return [
            _tag(
                Paragraph(
                    _inline_markup(node.children, base_url=base_url), styles[f"FuraH{level}"]
                ),
                f"H{level}",
                tag_records,
                outline=(outline_prefix, title, level),
            )
        ]
    if isinstance(node, ASTParagraph):
        markup = _inline_markup(node.children, base_url=base_url)
        return [_tag(Paragraph(markup or "&#160;", styles["FuraBody"]), "P", tag_records)]
    if isinstance(node, (FencedCode, IndentedCode)):
        code = node.get_code(source) if isinstance(node, FencedCode) else node.code
        return [
            _TagMarker("Code", tag_records),
            Preformatted(_wrap_code(code.rstrip()), styles["FuraCode"]),
        ]
    if isinstance(node, List):
        items: list[ListItem] = []
        for item_index, item in enumerate(node.items):
            children: list[Flowable] = []
            for child_index, child in enumerate(item.children):
                children.extend(
                    _block_flowables(
                        child,
                        source=source,
                        styles=styles,
                        tag_records=tag_records,
                        base_url=base_url,
                        outline_prefix=f"{outline_prefix}-{item_index}-{child_index}",
                        table_header=table_header,
                        table_grid=table_grid,
                    )
                )
            items.append(ListItem(children, leftIndent=14))
        rendered = ListFlowable(
            items,
            bulletType="1" if node.ordered else "bullet",
            start=str(node.start),
            leftIndent=18,
            bulletFontName="Helvetica",
            bulletFontSize=8,
            spaceAfter=6,
        )
        return [_TagMarker("L", tag_records), rendered]
    if isinstance(node, ASTTable):
        rows = [*node.head, *node.body]
        column_count = max((len(row.cells) for row in rows), default=1)
        data: list[list[Paragraph]] = []
        for row in rows:
            cells = [_table_cell_chunks(cell.children, base_url=base_url) for cell in row.cells]
            chunk_count = max((len(chunks) for chunks in cells), default=1)
            for chunk_index in range(chunk_count):
                data.append(
                    [
                        Paragraph(
                            chunks[chunk_index] if chunk_index < len(chunks) else "&#160;",
                            styles["FuraBody"],
                        )
                        for chunks in cells
                    ]
                )
        table = Table(
            data,
            colWidths=[6.55 * inch / column_count] * column_count,
            repeatRows=len(node.head),
            hAlign="LEFT",
        )
        table.setStyle(
            TableStyle(
                [
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, max(0, len(node.head) - 1)),
                        table_header,
                    ),
                    ("FONTNAME", (0, 0), (-1, max(0, len(node.head) - 1)), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.4, table_grid),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return [_TagMarker("Table", tag_records), table]
    if isinstance(node, Directive):
        label = (str(node.title or "") or node.name.replace("-", " ").title()).strip()
        items: list[Flowable] = [
            _tag(
                Paragraph(f"<b>{escape(label)}</b>", styles["FuraCallout"]),
                "Note",
                tag_records,
            )
        ]
        if node.raw_content and not node.children:
            items.append(
                _tag(
                    Paragraph(escape(_collapse(node.raw_content)), styles["FuraBody"]),
                    "P",
                    tag_records,
                )
            )
        for child_index, child in enumerate(node.children):
            items.extend(
                _block_flowables(
                    child,
                    source=source,
                    styles=styles,
                    tag_records=tag_records,
                    base_url=base_url,
                    outline_prefix=f"{outline_prefix}-{child_index}",
                    table_header=table_header,
                    table_grid=table_grid,
                )
            )
        return items
    if isinstance(node, BlockQuote):
        text = _inline_plain(node.children)
        return [
            _tag(
                Paragraph(f"<i>{escape(text)}</i>", styles["FuraCallout"]),
                "BlockQuote",
                tag_records,
            )
        ]
    if isinstance(node, ThematicBreak):
        return [Spacer(1, 0.12 * inch)]
    if isinstance(node, MathBlock):
        return [
            _TagMarker("Formula", tag_records),
            Preformatted(_wrap_code(node.content), styles["FuraCode"]),
        ]
    if isinstance(node, HtmlBlock):
        return [
            _tag(
                Paragraph("[HTML block omitted from PDF]", styles["FuraCallout"]),
                "Note",
                tag_records,
            )
        ]
    children = getattr(node, "children", ()) or ()
    items: list[Flowable] = []
    for child_index, child in enumerate(children):
        items.extend(
            _block_flowables(
                child,
                source=source,
                styles=styles,
                tag_records=tag_records,
                base_url=base_url,
                outline_prefix=f"{outline_prefix}-{child_index}",
                table_header=table_header,
                table_grid=table_grid,
            )
        )
    return items


def _inline_markup(nodes: Any, *, base_url: str) -> str:
    parts: list[str] = []
    for node in nodes or ():
        if isinstance(node, Text):
            parts.append(escape(node.content))
        elif isinstance(node, Strong):
            parts.append(f"<b>{_inline_markup(node.children, base_url=base_url)}</b>")
        elif isinstance(node, Emphasis):
            parts.append(f"<i>{_inline_markup(node.children, base_url=base_url)}</i>")
        elif isinstance(node, Strikethrough):
            parts.append(f"<strike>{_inline_markup(node.children, base_url=base_url)}</strike>")
        elif isinstance(node, CodeSpan):
            parts.append(f'<font name="Courier">{escape(node.code)}</font>')
        elif isinstance(node, Link):
            href = _canonical_url(base_url, str(node.url or ""))
            label = _inline_markup(node.children, base_url=base_url) or escape(href)
            visible_href = "" if _inline_plain(node.children) == href else f" ({escape(href)})"
            parts.append(f'<link href="{escape(href, quote=True)}">{label}</link>{visible_href}')
        elif isinstance(node, Image):
            label = str(node.alt or node.title or "figure")
            href = _canonical_url(base_url, str(node.url or ""))
            parts.append(
                f"<i>[Figure: {escape(label)}]</i> "
                f'<link href="{escape(href, quote=True)}">{escape(href)}</link>'
            )
        elif isinstance(node, SoftBreak):
            parts.append(" ")
        elif isinstance(node, LineBreak):
            parts.append("<br/>")
        else:
            children = getattr(node, "children", ()) or ()
            if children:
                parts.append(_inline_markup(children, base_url=base_url))
            elif getattr(node, "content", None):
                parts.append(escape(str(node.content)))
    return "".join(parts)


def _table_cell_chunks(nodes: Any, *, base_url: str, limit: int = 700) -> list[str]:
    markup = _inline_markup(nodes, base_url=base_url)
    if len(markup) <= limit:
        return [markup or "&#160;"]
    plain = _inline_plain(nodes)
    chunks = textwrap.wrap(
        plain,
        width=limit,
        break_long_words=True,
        break_on_hyphens=False,
        replace_whitespace=False,
    )
    return [escape(chunk) for chunk in chunks] or ["&#160;"]


def _inline_plain(nodes: Any) -> str:
    parts: list[str] = []
    for node in nodes or ():
        if isinstance(node, Text):
            parts.append(node.content)
        elif isinstance(node, CodeSpan):
            parts.append(node.code)
        elif isinstance(node, Image):
            parts.append(str(node.alt or node.title or "figure"))
        else:
            children = getattr(node, "children", ()) or ()
            if children:
                parts.append(_inline_plain(children))
            elif getattr(node, "content", None):
                parts.append(str(node.content))
    return _collapse("".join(parts))


def _canonical_url(base_url: str, value: str) -> str:
    if not value:
        return base_url or "about:blank"
    if value.startswith(("http://", "https://", "mailto:", "#")):
        return _clean_destination(value)
    if not base_url:
        return value
    return _clean_destination(urljoin(f"{base_url.rstrip('/')}/", value.lstrip("/")))


def _clean_destination(value: str) -> str:
    if not value.startswith(("http://", "https://")):
        return value
    parts = urlsplit(value)
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
        ],
        doseq=True,
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _document_title(nodes: list[Any], *, site_name: str, target: str) -> str:
    if target == "page" and len(nodes) == 1:
        return f"{nodes[0].title} - {site_name}"
    return f"{site_name} {target.title()} PDF"


def _document_subject(nodes: list[Any], *, base_url: str, target: str) -> str:
    if target == "page" and len(nodes) == 1:
        return _canonical_url(base_url, nodes[0].url)
    return f"{target} export of {len(nodes)} public catalog nodes"


def _document_language(nodes: list[Any]) -> str:
    languages = {str(getattr(node, "lang", "") or "") for node in nodes}
    return languages.pop() if len(languages) == 1 else "en"


def _apply_semantic_tags(path: Path, records: list[dict[str, Any]], *, lang: str) -> None:
    """Attach a minimal, honest tagged-PDF structure tree to marked content."""
    reader = PdfReader(str(path))
    writer = PdfWriter(clone_from=reader)
    structure_elements = ArrayObject()
    root = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/StructTreeRoot"),
            NameObject("/K"): structure_elements,
        }
    )
    root_ref = writer._add_object(root)
    page_records: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        page_records[int(record["page"])].append(record)
    parent_tree_nums = ArrayObject()
    for page_index, page in enumerate(writer.pages):
        page[NameObject("/StructParents")] = NumberObject(page_index)
        refs = ArrayObject()
        for record in sorted(page_records.get(page_index, ()), key=lambda item: item["mcid"]):
            element = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/StructElem"),
                    NameObject("/S"): NameObject(f"/{record['type']}"),
                    NameObject("/P"): root_ref,
                    NameObject("/Pg"): page.indirect_reference,
                    NameObject("/K"): NumberObject(int(record["mcid"])),
                }
            )
            element_ref = writer._add_object(element)
            structure_elements.append(element_ref)
            refs.append(element_ref)
        parent_tree_nums.extend((NumberObject(page_index), refs))
    parent_tree = DictionaryObject({NameObject("/Nums"): parent_tree_nums})
    root[NameObject("/ParentTree")] = writer._add_object(parent_tree)
    root[NameObject("/ParentTreeNextKey")] = NumberObject(len(writer.pages))
    writer.root_object[NameObject("/StructTreeRoot")] = root_ref
    writer.root_object[NameObject("/MarkInfo")] = DictionaryObject(
        {NameObject("/Marked"): BooleanObject(True)}
    )
    writer.root_object[NameObject("/Lang")] = TextStringObject(lang or "en")
    temporary = path.with_suffix(".tagged.pdf")
    with temporary.open("wb") as handle:
        writer.write(handle)
    temporary.replace(path)


def _wrap_code(code: str) -> str:
    wrapped: list[str] = []
    for line in code.splitlines():
        wrapped.extend(textwrap.wrap(line, width=92, replace_whitespace=False) or [""])
    return "\n".join(wrapped)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _page_footer(canvas: Any, doc: Any, *, pagesize: tuple[float, float]) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawRightString(pagesize[0] - 0.7 * inch, 0.42 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _filename_for_target(target: str, raw: str | None) -> str:
    if target == "site":
        return "site.pdf"
    return f"{target}-{_safe_name(raw or target)}.pdf"


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().strip("/").lower())
    return text.strip(".-_") or "export"


def _write_pdf_manifest(
    output_dir: Path,
    *,
    target: str,
    paths: tuple[Path, ...],
    nodes: list[Any],
    page_count: int,
    paper: str,
    grayscale: bool,
    base_url: str,
) -> None:
    artifact_fingerprints = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()[:16] for path in paths
    }
    write_deployment_manifest(
        output_dir / "manifest.json",
        DeploymentManifest(
            target="pdf",
            mode="pdf",
            page_count=page_count,
            artifacts=tuple(
                DeploymentArtifact(
                    path=path.name,
                    bytes=path.stat().st_size,
                    media_type="application/pdf",
                    fingerprint=artifact_fingerprints[path.name],
                )
                for path in paths
            ),
            fingerprints={"artifacts": artifact_fingerprints},
            sync={},
            extensions={
                "pdf": {
                    "paper": paper.lower(),
                    "grayscale": grayscale,
                    "physical_page_count": page_count,
                    "source_node_count": len(nodes),
                    "tagging_policy": "semantic-marked-content-v1",
                    "outline_policy": "catalog-node-and-authored-headings",
                    "degradation_policy": {
                        "figures": "linked-alt-text",
                        "html_blocks": "labeled-omission",
                        "directives": "titled-linearized-content",
                        "diagrams": "searchable-code-fallback",
                    },
                    "canonical_urls": [_canonical_url(base_url, node.url) for node in nodes],
                },
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
            },
        ),
    )


def _update_public_channel_manifest(
    catalog: Any,
    *,
    config: Any | None,
    output_dir: Path,
    paths: tuple[Path, ...],
    base_url: str,
) -> None:
    public_root = output_dir.parent if output_dir.name == "pdf" else output_dir
    export_manifest_path = public_root / "export.manifest.json"
    export_manifest = read_deployment_manifest(
        export_manifest_path,
        target_hint="static",
    )
    artifact_paths: list[str] = []
    fingerprints: dict[str, str] = {}
    if export_manifest is not None:
        artifact_paths = list(export_manifest.artifact_paths)
        fingerprints = export_manifest.route_fingerprints
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
        mode="static" if export_manifest is not None else "pdf",
        paths=sorted(artifact_paths),
        pdf_paths=pdf_paths,
        fingerprints=fingerprints,
    )
    write_deployment_manifest(
        public_root / "channels.json",
        DeploymentManifest.from_dict(payload, target_hint="channels"),
    )
