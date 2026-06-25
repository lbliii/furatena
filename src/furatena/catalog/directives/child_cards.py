"""child-cards directive — card grid from furatena.catalog child pages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from patitas.directives.options import StyledOptions
from patitas.nodes import Directive

from furatena.catalog.context import child_stubs, get_render_context
from furatena.catalog.directives.html import GAP_CHIRPUI
from furatena.catalog.directives.kida_render import render_directive

if TYPE_CHECKING:
    from collections.abc import Sequence

    from patitas.location import SourceLocation
    from patitas.nodes import Block
    from patitas.stringbuilder import StringBuilder


@dataclass(frozen=True, slots=True)
class ChildCardsOptions(StyledOptions):
    columns: str = "auto"
    gap: str = "medium"
    include: str = "all"
    fields: str = "title, description"


@dataclass(frozen=True, slots=True)
class ChildCardsHandler:
    names: ClassVar[tuple[str, ...]] = ("child-cards",)
    token_type: ClassVar[str] = "child_cards"
    contract: ClassVar[None] = None
    options_class: ClassVar[type[ChildCardsOptions]] = ChildCardsOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: ChildCardsOptions,
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
        if ctx is None:
            sb.append(_empty("No page context available"))
            return

        children = child_stubs(ctx.current_slug)
        if not children:
            sb.append(_empty("No child pages found"))
            return

        cols = opts.columns if opts.columns.isdigit() else "2"
        cards = [
            {
                "title": stub.title,
                "description": stub.description,
                "href": stub.url,
                "type": stub.page_type,
            }
            for stub in children
        ]
        sb.append(
            render_directive(
                "child_cards",
                cols=cols,
                gap=GAP_CHIRPUI.get(opts.gap, "md"),
                extra_class=opts.class_ or "",
                cards=cards,
            )
        )


def _empty(message: str) -> str:
    return f'<p class="chirpui-text-muted"><em>{message}</em></p>'
