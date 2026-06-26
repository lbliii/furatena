"""include directive — recursive markdown fragments from content/chirp."""

from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
from typing import TYPE_CHECKING, Any, ClassVar

from patitas.directives.options import DirectiveOptions
from patitas.nodes import Directive

from furatena.catalog.context import get_render_context
from furatena.catalog.files import (
    read_bounded_text,
    resolve_content_path,
    slice_lines,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from patitas.location import SourceLocation
    from patitas.nodes import Block
    from patitas.stringbuilder import StringBuilder

MAX_INCLUDE_DEPTH = 10


@dataclass(frozen=True, slots=True)
class IncludeOptions(DirectiveOptions):
    start_line: int | None = None
    end_line: int | None = None
    file_path: str = ""


@dataclass(frozen=True, slots=True)
class IncludeHandler:
    names: ClassVar[tuple[str, ...]] = ("include",)
    token_type: ClassVar[str] = "include"
    contract: ClassVar[None] = None
    options_class: ClassVar[type[IncludeOptions]] = IncludeOptions
    preserves_raw_content: ClassVar[bool] = True

    def parse(
        self,
        name: str,
        title: str | None,
        options: IncludeOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        file_path = title.strip() if title else ""
        computed = replace(options, file_path=file_path)
        return Directive(
            location=location,
            name=name,
            title=title,
            options=computed,
            children=tuple(children),
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
            sb.append(_error("Include requires render context"))
            return
        if ctx.include_depth >= MAX_INCLUDE_DEPTH:
            sb.append(_error(f"Maximum include depth ({MAX_INCLUDE_DEPTH}) exceeded"))
            return

        resolved = resolve_content_path(ctx.content_root, ctx.source_rel, file_path)
        if resolved is None:
            sb.append(_error(f"Could not resolve include path: {file_path}"))
            return

        rel = str(resolved.relative_to(ctx.content_root.resolve()))
        if rel in ctx.include_stack:
            sb.append(_error(f"Include cycle detected: {rel}"))
            return

        try:
            raw = read_bounded_text(resolved)
        except OSError as exc:
            sb.append(_error(f"Could not read {file_path}: {exc}"))
            return

        content = slice_lines(raw, opts.start_line, opts.end_line)
        stack = set(ctx.include_stack)
        stack.add(rel)
        html = ctx.render_markdown(content, rel, ctx.current_slug, stack, ctx.include_depth + 1)
        sb.append(html)


def _error(message: str) -> str:
    return (
        f'<aside class="chirpui-callout chirpui-callout--error">'
        f'<div class="chirpui-callout__body"><strong>Include error:</strong> {escape(message)}</div>'
        f"</aside>"
    )
