"""Inventory domain roles — ``{py}`os.path.join```."""

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
class InventoryRole:
    """Resolve Sphinx-style domain targets from loaded inventories."""

    names: ClassVar[tuple[str, ...]] = ()
    token_type: ClassVar[str] = "inv"
    domain: ClassVar[str] = "py"

    def parse(self, name: str, content: str, location: SourceLocation) -> Role:
        target = content.strip()
        return Role(location=location, name=name, content=target, target=target)

    def render(self, node: Role, sb: StringBuilder) -> None:
        ctx = get_reference_context()
        catalog = ctx.catalog if ctx is not None else None
        inventory = ctx.inventory_store if ctx is not None else None
        target = node.content or node.target or ""
        role_name = node.name or self.domain
        query = target if ":" in target else f"{role_name}:{target}"
        resolved = resolve_reference(
            query,
            catalog=catalog,
            inventory_store=inventory,
            role_name=role_name,
        )
        sb.append(render_reference_html(resolved))


@dataclass(frozen=True, slots=True)
class PyRole(InventoryRole):
    names = ("py",)
    token_type = "py"
    domain = "py"
