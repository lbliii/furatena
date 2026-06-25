"""Admonition directives → chirp-ui callouts via Kida."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from patitas.directives.contracts import DirectiveContract
from patitas.directives.options import AdmonitionOptions
from patitas.nodes import Directive

from furatena.catalog.directives.kida_render import render_directive

if TYPE_CHECKING:
    from collections.abc import Sequence

    from patitas.location import SourceLocation
    from patitas.nodes import Block
    from patitas.stringbuilder import StringBuilder

_NAMES = (
    "note",
    "tip",
    "warning",
    "danger",
    "error",
    "info",
    "important",
    "example",
    "success",
    "caution",
    "seealso",
)

_VARIANT = {
    "note": "info",
    "info": "info",
    "important": "info",
    "seealso": "info",
    "tip": "success",
    "success": "success",
    "warning": "warning",
    "caution": "warning",
    "danger": "error",
    "error": "error",
    "example": "neutral",
}


@dataclass(frozen=True, slots=True)
class AdmonitionHandler:
    names: ClassVar[tuple[str, ...]] = _NAMES
    token_type: ClassVar[str] = "admonition"
    contract: ClassVar[DirectiveContract | None] = None
    options_class: ClassVar[type[AdmonitionOptions]] = AdmonitionOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: AdmonitionOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        effective_title = title if title else name.replace("seealso", "See also").capitalize()
        return Directive(
            location=location,
            name=name,
            title=effective_title,
            options=options,
            children=tuple(children),
        )

    def render(
        self,
        node: Directive[Any],
        rendered_children: str,
        sb: StringBuilder,
    ) -> None:
        opts = node.options
        sb.append(
            render_directive(
                "callout",
                variant=_VARIANT.get(node.name, "info"),
                admonition_name=node.name,
                title=node.title,
                body=rendered_children,
                extra_class=opts.class_ or "",
            )
        )
