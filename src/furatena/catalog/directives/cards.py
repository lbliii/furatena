"""Cards grid directives → chirp-ui card_link / card grid via Kida."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, ClassVar

from patitas.directives.contracts import DirectiveContract
from patitas.directives.options import StyledOptions
from patitas.nodes import Directive

from furatena.catalog.directives.html import GAP_CHIRPUI, render_inline_text, rewrite_href
from furatena.catalog.directives.icons import render_icon_html
from furatena.catalog.directives.kida_render import as_markup, render_directive

if TYPE_CHECKING:
    from collections.abc import Sequence

    from patitas.location import SourceLocation
    from patitas.nodes import Block
    from patitas.stringbuilder import StringBuilder

CARDS_CONTRACT = DirectiveContract(requires_children=("card",), allows_children=("card",))
CARD_CONTRACT = DirectiveContract(requires_parent=("cards",))


@dataclass(frozen=True, slots=True)
class CardsOptions(StyledOptions):
    columns: str = "auto"
    gap: str = "medium"


@dataclass(frozen=True, slots=True)
class CardOptions(StyledOptions):
    icon: str = ""
    link: str = ""
    description: str = ""
    badge: str = ""


@dataclass(frozen=True, slots=True)
class CardsHandler:
    names: ClassVar[tuple[str, ...]] = ("cards",)
    token_type: ClassVar[str] = "cards_grid"
    contract: ClassVar[DirectiveContract | None] = CARDS_CONTRACT
    options_class: ClassVar[type[CardsOptions]] = CardsOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: CardsOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        cols = options.columns if options.columns.isdigit() else "2"
        return Directive(
            location=location,
            name=name,
            title=title,
            options=replace(options, columns=cols),
            children=tuple(children),
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        opts = node.options
        sb.append(
            render_directive(
                "card_grid",
                cols=opts.columns,
                gap=GAP_CHIRPUI.get(opts.gap, "md"),
                body=rendered_children,
                extra_class=opts.class_ or "",
            )
        )


@dataclass(frozen=True, slots=True)
class CardHandler:
    names: ClassVar[tuple[str, ...]] = ("card",)
    token_type: ClassVar[str] = "card"
    contract: ClassVar[DirectiveContract | None] = CARD_CONTRACT
    options_class: ClassVar[type[CardOptions]] = CardOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: CardOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        return Directive(
            location=location,
            name=name,
            title=title or "",
            options=options,
            children=tuple(children),
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        opts = node.options
        title = as_markup(render_inline_text(node.title or "Card"))
        subtitle = as_markup(render_inline_text(opts.description)) if opts.description else ""
        if opts.link:
            sb.append(
                render_directive(
                    "card_link",
                    href=rewrite_href(opts.link),
                    title=title,
                    subtitle=subtitle,
                    badge_text=opts.badge,
                    icon_html=render_icon_html(opts.icon),
                    body=rendered_children,
                    extra_class=opts.class_ or "",
                )
            )
        else:
            sb.append(
                render_directive(
                    "card_static",
                    title=title,
                    subtitle=subtitle,
                    icon_html=render_icon_html(opts.icon),
                    body=rendered_children,
                    extra_class=opts.class_ or "",
                )
            )
