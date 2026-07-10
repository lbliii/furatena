"""MDX documentation adapter (JSX-aware markdown)."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from furatena.catalog.models import ContentDirective, ContentIR
from furatena.catalog.render import DocsRenderer
from furatena.catalog.sources.adapters.markdown import PatitasMarkdownAdapter
from furatena.catalog.sources.types import AdaptedContent, PageSource

_JSX_SELF_CLOSING = re.compile(
    r"<([A-Z][A-Za-z0-9]*)\s([^>]*?)/>",
)
_JSX_BLOCK = re.compile(
    r"<([A-Z][A-Z_a-z0-9]*)\b([^>]*)>(.*?)</\1>",
    re.DOTALL,
)


def _jsx_attrs(raw: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for match in re.finditer(
        r'([A-Za-z_:][\w:-]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\{[^}]+\}))', raw
    ):
        key = match.group(1)
        value = match.group(2) or match.group(3) or match.group(4) or ""
        options[key] = value.strip()
    return options


def _line_for_match(source: str, match: re.Match[str]) -> int:
    return source.count("\n", 0, match.start()) + 1


def mdx_to_markdown(source: str) -> str:
    """Lower JSX components into MyST-style extension blocks for Patitas."""
    lines: list[str] = []
    last = 0
    for match in _JSX_BLOCK.finditer(source):
        lines.append(source[last : match.start()])
        name = match.group(1)
        options = _jsx_attrs(match.group(2) or "")
        inner = (match.group(3) or "").strip()
        option_lines = "\n".join(f":{key}: {value}" for key, value in options.items())
        block = f":::{name.lower()}"
        if option_lines:
            block = f"{block}\n{option_lines}"
        block = f"{block}\n{inner}\n:::"
        lines.append(block)
        last = match.end()
    tail = source[last:]
    for _match in _JSX_SELF_CLOSING.finditer(tail):
        pass
    converted = "".join(lines) + tail

    def replace_self_closing(match: re.Match[str]) -> str:
        name = match.group(1)
        options = _jsx_attrs(match.group(2) or "")
        option_lines = "\n".join(f":{key}: {value}" for key, value in options.items())
        block = f":::{name.lower()}"
        if option_lines:
            block = f"{block}\n{option_lines}"
        return f"{block}\n:::"

    return _JSX_SELF_CLOSING.sub(replace_self_closing, converted)


class MdxAdapter:
    """Adapt MDX by lowering JSX to markdown extension blocks."""

    content_format = "mdx"

    def __init__(self, renderer: DocsRenderer | None = None) -> None:
        self._markdown = PatitasMarkdownAdapter(renderer=renderer)

    def parse(self, body: str) -> tuple[object | None, ContentIR | None]:
        return self._markdown.parse(mdx_to_markdown(body))

    def parse_incremental(
        self,
        body: str,
        previous: object | None,
        *,
        previous_body: str = "",
    ) -> tuple[object | None, ContentIR | None]:
        return self._markdown.parse_incremental(
            mdx_to_markdown(body),
            previous,
            previous_body=mdx_to_markdown(previous_body) if previous_body else "",
        )

    def invalidation_regions(self, old: object | None, new: object | None) -> set[str]:
        return set(self._markdown.invalidation_regions(old, new))

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
        lowered = mdx_to_markdown(source.body)
        lowered_source = PageSource(
            path=source.path,
            content_format="patitas-markdown",
            meta=source.meta,
            body=lowered,
            source_path=source.source_path,
            url=source.url,
            slug=source.slug,
        )
        adapted = self._markdown.adapt(
            lowered_source,
            stubs=stubs,
            render_markdown=render_markdown,
            get_backlinks=get_backlinks,
            content_root=content_root,
            include_stack=include_stack,
            include_depth=include_depth,
            document=document,
            mount=mount,
        )
        return AdaptedContent(
            body_html=adapted.body_html,
            content_ir=self._with_mdx_extensions(adapted.content_ir, source.body),
            toc=adapted.toc,
            body_text=adapted.body_text,
            sections=adapted.sections,
            native_ast=adapted.native_ast,
            native_document=adapted.native_document,
        )

    def _with_mdx_extensions(self, content_ir: ContentIR | None, raw: str) -> ContentIR | None:
        if content_ir is None:
            return None
        jsx_extensions = []
        for pattern in (_JSX_BLOCK, _JSX_SELF_CLOSING):
            for match in pattern.finditer(raw):
                jsx_extensions.append(
                    ContentDirective(
                        name=match.group(1),
                        options=_jsx_attrs(match.group(2) or ""),
                        line=_line_for_match(raw, match),
                    )
                )
        if not jsx_extensions:
            return content_ir
        merged = tuple(content_ir.directives) + tuple(jsx_extensions)
        return ContentIR(
            headings=content_ir.headings,
            links=content_ir.links,
            directives=merged,
        )
