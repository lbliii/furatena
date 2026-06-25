"""Dropdown directive → chirp-ui collapse via Kida, chirp-theme visual skin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from patitas.directives.options import StyledOptions
from patitas.nodes import Directive

from furatena.catalog.directives.html import render_inline_text
from furatena.catalog.directives.icons import render_icon_html
from furatena.catalog.directives.kida_render import as_markup, render_directive

if TYPE_CHECKING:
    from collections.abc import Sequence

    from patitas.location import SourceLocation
    from patitas.nodes import Block
    from patitas.stringbuilder import StringBuilder

DROPDOWN_COLORS = frozenset({"success", "warning", "danger", "info", "minimal"})


@dataclass(frozen=True, slots=True)
class DropdownOptions(StyledOptions):
    open: bool = False
    icon: str | None = None
    badge: str | None = None
    color: str | None = None
    description: str | None = None


def _dropdown_icon_html(icon_name: str) -> str:
    return render_icon_html(icon_name)


@dataclass(frozen=True, slots=True)
class DropdownHandler:
    names: ClassVar[tuple[str, ...]] = ("dropdown",)
    token_type: ClassVar[str] = "dropdown"
    contract: ClassVar[None] = None
    options_class: ClassVar[type[DropdownOptions]] = DropdownOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: DropdownOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        return Directive(
            location=location,
            name=name,
            title=title or "Details",
            options=options,
            children=tuple(children),
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        opts = node.options
        color = opts.color if opts.color in DROPDOWN_COLORS else ""
        title = as_markup(render_inline_text(node.title or "Details"))
        description = (
            as_markup(render_inline_text(opts.description))
            if opts.description
            else ""
        )
        sb.append(
            render_directive(
                "accordion",
                title=title,
                body=rendered_children,
                open=opts.open,
                description=description,
                badge=opts.badge or "",
                color=color,
                icon=_dropdown_icon_html(opts.icon) if opts.icon else "",
                extra_class=opts.class_ or "",
            )
        )
