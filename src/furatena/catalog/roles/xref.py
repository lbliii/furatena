"""Inline cross-reference role — ``{xref}`mount:slug```."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from patitas.nodes import Role

from furatena.catalog.references.context import get_reference_context
from furatena.catalog.references.resolver import render_reference_html, resolve_reference

if TYPE_CHECKING:
    from patitas.location import SourceLocation
    from patitas.stringbuilder import StringBuilder


@dataclass(frozen=True, slots=True)
class XrefRole:
    """Resolve mount-qualified or slug targets via the catalog graph."""

    names: ClassVar[tuple[str, ...]] = ("xref", "doc")
    token_type: ClassVar[str] = "xref"

    def parse(self, name: str, content: str, location: SourceLocation) -> Role:
        target = content.strip()
        return Role(location=location, name=name, content=target, target=target)

    def render(self, node: Role, sb: StringBuilder) -> None:
        ctx = get_reference_context()
        catalog = ctx.catalog if ctx is not None else None
        inventory = ctx.inventory_store if ctx is not None else None
        target = node.content or node.target or ""
        resolved = resolve_reference(
            target,
            catalog=catalog,
            inventory_store=inventory,
            role_name=node.name,
        )
        sb.append(render_reference_html(resolved))
