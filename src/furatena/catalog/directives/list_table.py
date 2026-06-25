"""list-table directive → chirp-ui table."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from patitas.directives.options import DirectiveOptions
from patitas.nodes import Directive

from furatena.catalog.directives.html import render_inline_cell
from furatena.catalog.directives.kida_render import as_markup, render_directive

if TYPE_CHECKING:
    from collections.abc import Sequence

    from patitas.location import SourceLocation
    from patitas.nodes import Block
    from patitas.stringbuilder import StringBuilder


@dataclass(frozen=True, slots=True)
class ListTableOptions(DirectiveOptions):
    _aliases: ClassVar[dict[str, str]] = {
        "header-rows": "header_rows",
        "class": "css_class",
    }

    header_rows: int = 0
    widths: str = ""
    css_class: str = ""


@dataclass(frozen=True, slots=True)
class ListTableHandler:
    names: ClassVar[tuple[str, ...]] = ("list-table",)
    token_type: ClassVar[str] = "list_table"
    contract: ClassVar[None] = None
    options_class: ClassVar[type[ListTableOptions]] = ListTableOptions
    preserves_raw_content: ClassVar[bool] = True

    def parse(
        self,
        name: str,
        title: str | None,
        options: ListTableOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        return Directive(
            location=location,
            name=name,
            title=title,
            options=options,
            children=tuple(children),
            raw_content=content,
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        del rendered_children
        opts = node.options
        rows = _parse_list_rows(node.raw_content or "")
        if not rows:
            sb.append('<p class="fura-error">List table has no rows</p>')
            return

        header_rows = max(0, opts.header_rows)
        widths: list[str] = []
        if opts.widths:
            try:
                widths = [f"{int(part)}%" for part in opts.widths.split()]
            except ValueError:
                widths = []

        rendered_rows = [
            [as_markup(render_inline_cell(cell)) for cell in row] for row in rows
        ]
        headers = rendered_rows[0] if header_rows > 0 else None
        body_rows = rendered_rows[header_rows:] if header_rows > 0 else rendered_rows

        sb.append(
            render_directive(
                "table",
                headers=headers,
                body_rows=body_rows,
                widths=widths or None,
                caption=node.title or "",
                extra_class=opts.css_class or "",
            )
        )


def _parse_list_rows(content: str) -> list[list[str]]:
    rows: list[list[str]] = []
    current_row: list[str] = []
    current_cell_lines: list[str] = []

    for line in content.split("\n"):
        stripped = line.strip()

        if re.match(r"^\*\s+-\s*", line):
            if current_cell_lines:
                current_row.append("\n".join(current_cell_lines).strip())
                current_cell_lines = []
            if current_row:
                rows.append(current_row)
                current_row = []
            cell_content = re.sub(r"^\*\s+-\s*", "", line).strip()
            current_cell_lines = [cell_content] if cell_content else []

        elif re.match(r"^  -\s*", line):
            if current_cell_lines:
                current_row.append("\n".join(current_cell_lines).strip())
                current_cell_lines = []
            cell_content = re.sub(r"^  -\s*", "", line).strip()
            current_cell_lines = [cell_content] if cell_content else []

        elif not stripped and current_cell_lines:
            current_cell_lines.append("")

        elif line.startswith("    ") and current_cell_lines:
            current_cell_lines.append(line[4:])

    if current_cell_lines:
        current_row.append("\n".join(current_cell_lines).strip())
    if current_row:
        rows.append(current_row)

    return rows
