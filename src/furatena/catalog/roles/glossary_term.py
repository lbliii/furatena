"""Inline glossary term role — {gterm}`Term`."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, ClassVar

from patitas.nodes import Role

from furatena.catalog.context import get_render_context
from furatena.catalog.glossary_data import lookup_term, repo_root_for_content

if TYPE_CHECKING:
    from patitas.location import SourceLocation
    from patitas.stringbuilder import StringBuilder


@dataclass(frozen=True, slots=True)
class GtermRole:
    """Render inline glossary lookups as annotated terms."""

    names: ClassVar[tuple[str, ...]] = ("gterm", "term")
    token_type: ClassVar[str] = "gterm"

    def parse(
        self,
        name: str,
        content: str,
        location: SourceLocation,
    ) -> Role:
        label = content.strip()
        return Role(
            location=location,
            name=name,
            content=label,
            target=label,
        )

    def render(self, node: Role, sb: StringBuilder) -> None:
        label = node.content or node.target or ""
        ctx = get_render_context()
        site_root = repo_root_for_content(ctx.content_root) if ctx else None
        entry = lookup_term(site_root, label) if site_root is not None else None
        if entry is None:
            sb.append(f"<span>{escape(label)}</span>")
            return
        term = str(entry.get("term", label))
        definition = str(entry.get("definition", ""))
        sb.append(
            f'<abbr class="chirp-theme-glossary-term" title="{escape(definition, quote=True)}">'
            f"{escape(term)}</abbr>"
        )
