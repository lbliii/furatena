"""Glossary directive — terms from data/glossary.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, Any, ClassVar

from patitas.directives.options import DirectiveOptions
from patitas.nodes import Directive

from furatena.catalog.context import get_render_context
from furatena.catalog.directives.kida_render import render_directive
from furatena.catalog.glossary_data import DEFAULT_PATH, filter_terms, load_glossary_terms, repo_root_for_content

if TYPE_CHECKING:
    from collections.abc import Sequence

    from patitas.location import SourceLocation
    from patitas.nodes import Block
    from patitas.stringbuilder import StringBuilder

_INLINE_CODE = re.compile(r"`([^`]+)`")
_INLINE_BOLD = re.compile(r"\*\*([^*]+)\*\*")


@dataclass(frozen=True, slots=True)
class GlossaryOptions(DirectiveOptions):
    _aliases: ClassVar[dict[str, str]] = {"show-tags": "show_tags"}

    tags: str = ""
    sorted: bool = False
    show_tags: bool = False
    collapsed: bool = False
    limit: int = 0
    source: str = DEFAULT_PATH


def _render_tags(tags: list[str]) -> str:
    if not tags:
        return ""
    chips = "".join(
        f'<span class="chirp-theme-directive-glossary__tag">{escape(tag)}</span>'
        for tag in tags
    )
    return f'<div class="chirp-theme-directive-glossary__tags">{chips}</div>'


def _render_definition(text: str) -> str:
    html = escape(text)
    html = _INLINE_CODE.sub(r"<code>\1</code>", html)
    html = _INLINE_BOLD.sub(r"<strong>\1</strong>", html)
    return html


def _term_definition_html(item: dict[str, object], *, show_tags: bool) -> str:
    definition = _render_definition(str(item.get("definition", "")))
    if show_tags:
        tags = [str(tag) for tag in (item.get("tags") or []) if str(tag).strip()]
        definition += _render_tags(tags)
    return definition


@dataclass(frozen=True, slots=True)
class GlossaryHandler:
    names: ClassVar[tuple[str, ...]] = ("glossary",)
    token_type: ClassVar[str] = "glossary"
    contract: ClassVar[None] = None
    options_class: ClassVar[type[GlossaryOptions]] = GlossaryOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: GlossaryOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        return Directive(
            location=location,
            name=name,
            title=title,
            options=options,
            children=(),
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        del rendered_children
        ctx = get_render_context()
        opts = node.options
        tag_list = [t.strip() for t in opts.tags.split(",") if t.strip()]
        if not tag_list:
            sb.append(_error("No tags specified. Use :tags: to filter glossary terms."))
            return

        site_root = repo_root_for_content(ctx.content_root) if ctx else None
        if site_root is None:
            sb.append(_error("Glossary requires render context"))
            return

        all_terms = load_glossary_terms(site_root, opts.source)
        terms = filter_terms(all_terms, tag_list)
        if not terms:
            sb.append(_error(f"No glossary terms match tags: {', '.join(tag_list)}"))
            return

        if opts.sorted:
            terms = sorted(terms, key=lambda t: str(t.get("term", "")).lower())

        if opts.limit > 0:
            terms = terms[: opts.limit]

        items = [
            {
                "term": str(item.get("term", "")),
                "definition_html": _term_definition_html(item, show_tags=opts.show_tags),
            }
            for item in terms
        ]
        summary = f"Key terms ({len(items)})"
        sb.append(
            render_directive(
                "glossary",
                terms=items,
                collapsed=opts.collapsed,
                summary=summary,
            )
        )


def _error(message: str) -> str:
    return (
        f'<aside class="chirpui-callout chirpui-callout--warning">'
        f'<div class="chirpui-callout__body">{escape(message)}</div>'
        f"</aside>"
    )
