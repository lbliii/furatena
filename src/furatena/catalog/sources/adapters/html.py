"""HTML documentation adapter."""

from __future__ import annotations

from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path

from furatena.catalog.content_ir import content_ir_to_toc, slugify_heading
from furatena.catalog.models import ContentDirective, ContentHeading, ContentIR, ContentLink
from furatena.catalog.sources.ir_diff import htmx_swap_hints, ir_invalidation_regions
from furatena.catalog.sources.types import AdaptedContent, PageSource
from furatena.catalog.text import derive_body_text, derive_sections

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


class _HTMLContentExtractor(HTMLParser):
    """Walk HTML and collect headings, links, and data-component extensions."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[ContentHeading] = []
        self.links: list[ContentLink] = []
        self.extensions: list[ContentDirective] = []
        self._text_parts: list[str] = []
        self._current_heading_level: int | None = None
        self._current_heading_parts: list[str] = []
        self._line = 1

    @property
    def plain_text(self) -> str:
        return " ".join(part.strip() for part in self._text_parts if part.strip())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: (value or "") for key, value in attrs}
        if tag in _HEADING_TAGS:
            self._current_heading_level = int(tag[1])
            self._current_heading_parts = []
        if tag == "a" and attr_map.get("href"):
            self.links.append(
                ContentLink(
                    href=attr_map["href"],
                    text="",
                    line=self._line,
                )
            )
        component = attr_map.get("data-component") or attr_map.get("data-directive")
        if component:
            options = {
                key.removeprefix("data-"): value
                for key, value in attr_map.items()
                if key.startswith("data-") and key not in {"data-component", "data-directive"}
            }
            self.extensions.append(
                ContentDirective(
                    name=component,
                    options=options,
                    line=self._line,
                )
            )

    def handle_endtag(self, tag: str) -> None:
        if tag in _HEADING_TAGS and self._current_heading_level is not None:
            text = "".join(self._current_heading_parts).strip()
            if text:
                anchor = slugify_heading(text)
                self.headings.append(
                    ContentHeading(
                        level=self._current_heading_level,
                        text=text,
                        anchor=anchor,
                        line=self._line,
                    )
                )
            self._current_heading_level = None
            self._current_heading_parts = []

    def handle_data(self, data: str) -> None:
        if not data:
            return
        self._text_parts.append(data)
        if self._current_heading_level is not None:
            self._current_heading_parts.append(data)
        if self.links and not self.links[-1].text:
            self.links[-1] = ContentLink(
                href=self.links[-1].href,
                text=data.strip(),
                line=self.links[-1].line,
            )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def extract_html_content_ir(html: str) -> ContentIR:
    parser = _HTMLContentExtractor()
    parser.feed(html)
    return ContentIR(
        headings=tuple(parser.headings),
        links=tuple(parser.links),
        directives=tuple(parser.extensions),
    )


def html_body_text(html: str) -> str:
    parser = _HTMLContentExtractor()
    parser.feed(html)
    return parser.plain_text


def wrap_html_body(html: str) -> str:
    stripped = html.strip()
    if not stripped:
        return ""
    if stripped.startswith("<article"):
        return stripped
    return f'<article class="doc-html">{stripped}</article>'


class HtmlAdapter:
    """Adapt raw HTML pages into normalized catalog content."""

    content_format = "html"

    def parse(self, body: str) -> tuple[ContentIR | None, ContentIR | None]:
        content_ir = extract_html_content_ir(body)
        return content_ir, content_ir

    def parse_incremental(
        self,
        body: str,
        previous: object | None,
        *,
        previous_body: str = "",
    ) -> tuple[ContentIR | None, ContentIR | None]:
        _ = previous_body
        return self.parse(body)

    def invalidation_regions(self, old: object | None, new: object | None) -> set[str]:
        old_ir = old if isinstance(old, ContentIR) else None
        new_ir = new if isinstance(new, ContentIR) else None
        if new_ir is None and new is not None:
            new_ir = extract_html_content_ir(str(new))
        return set(ir_invalidation_regions(old_ir, new_ir))

    def invalidation_hints(self, old: object | None, new: object | None) -> tuple[str, ...]:
        return htmx_swap_hints(frozenset(self.invalidation_regions(old, new)))

    def adapt(
        self,
        source: PageSource,
        *,
        stubs,
        render_markdown: Callable[..., str],
        get_backlinks: Callable[[str], list[dict[str, str]]],
        content_root: Path,
        include_stack: set[str] | None = None,
        include_depth: int = 0,
        document: object | None = None,
        mount: str | None = None,
    ) -> AdaptedContent:
        _ = (
            stubs,
            render_markdown,
            get_backlinks,
            content_root,
            include_stack,
            include_depth,
            mount,
        )
        content_ir = (
            document if isinstance(document, ContentIR) else extract_html_content_ir(source.body)
        )
        body_html = wrap_html_body(source.body)
        toc = content_ir_to_toc(content_ir)
        body_text = html_body_text(source.body)
        sections = derive_sections(content_ir, None, source=source.body)
        if not body_text:
            body_text = derive_body_text(
                content_ir, None, source=source.body, description=source.meta.get("description", "")
            )
        return AdaptedContent(
            body_html=body_html,
            content_ir=content_ir,
            toc=toc,
            body_text=body_text,
            sections=sections,
            native_document=content_ir,
        )
