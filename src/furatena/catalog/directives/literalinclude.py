"""literalinclude directive — highlighted code from content/chirp files."""

from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from patitas.directives.options import DirectiveOptions
from patitas.nodes import Directive

from furatena.catalog.context import get_render_context
from furatena.catalog.directives.kida_render import render_directive, trusted_renderer_html
from furatena.catalog.files import read_bounded_text, resolve_content_path, slice_lines
from furatena.catalog.highlight import highlight_code_block

if TYPE_CHECKING:
    from collections.abc import Sequence

    from patitas.location import SourceLocation
    from patitas.nodes import Block
    from patitas.stringbuilder import StringBuilder

EXTENSION_LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".html": "html",
    ".css": "css",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".md": "markdown",
    ".sh": "bash",
    ".bash": "bash",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".sql": "sql",
}


@dataclass(frozen=True, slots=True)
class LiteralIncludeOptions(DirectiveOptions):
    language: str = ""
    start_line: int | None = None
    end_line: int | None = None
    caption: str = ""
    file_path: str = ""


@dataclass(frozen=True, slots=True)
class LiteralIncludeHandler:
    names: ClassVar[tuple[str, ...]] = ("literalinclude",)
    token_type: ClassVar[str] = "literalinclude"
    contract: ClassVar[None] = None
    options_class: ClassVar[type[LiteralIncludeOptions]] = LiteralIncludeOptions
    preserves_raw_content: ClassVar[bool] = True

    def parse(
        self,
        name: str,
        title: str | None,
        options: LiteralIncludeOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        file_path = title.strip() if title else ""
        language = options.language
        if not language and file_path:
            language = EXTENSION_LANGUAGE_MAP.get(Path(file_path).suffix.lower(), "")
        computed = replace(options, file_path=file_path, language=language)
        return Directive(
            location=location,
            name=name,
            title=title,
            options=computed,
            children=(),
            raw_content=content,
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        del rendered_children
        ctx = get_render_context()
        opts = node.options
        file_path = opts.file_path
        if not file_path:
            sb.append(_error("No file path specified"))
            return
        if ctx is None:
            sb.append(_error("Literal include requires render context"))
            return

        resolved = resolve_content_path(ctx.content_root, ctx.source_rel, file_path)
        if resolved is None:
            sb.append(_error(f"Could not resolve path: {file_path}"))
            return

        try:
            raw = read_bounded_text(resolved)
        except OSError as exc:
            sb.append(_error(f"Could not read {file_path}: {exc}"))
            return

        code = slice_lines(raw, opts.start_line, opts.end_line).rstrip("\n")
        panel = highlight_code_block(opts.language or "text", code)
        sb.append(
            render_directive(
                "literalinclude",
                caption=opts.caption,
                body=trusted_renderer_html(panel),
            )
        )


def _error(message: str) -> str:
    return (
        f'<aside class="chirpui-callout chirpui-callout--error">'
        f'<div class="chirpui-callout__body"><strong>Literal include error:</strong> {escape(message)}</div>'
        f"</aside>"
    )
