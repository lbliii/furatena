"""List-table inline markdown rendering."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.directives.html import render_inline_cell
from furatena.catalog.directives.kida_render import as_markup, render_directive


def test_render_inline_cell_preserves_markers_inside_code() -> None:
    html = render_inline_cell(
        "`Template.inline(source, **ctx)` / `InlineTemplate(source, **ctx)`"
    )
    assert html == (
        "<code>Template.inline(source, **ctx)</code> / "
        "<code>InlineTemplate(source, **ctx)</code>"
    )


def test_list_table_cells_are_not_double_escaped() -> None:
    cell = as_markup(render_inline_cell("`App`"))
    html = render_directive(
        "table",
        headers=[as_markup("Name"), as_markup("Job")],
        body_rows=[[cell, as_markup("The application.")]],
        widths=None,
        extra_class="",
    )
    assert "<code>App</code>" in html
    assert "&lt;code&gt;" not in html
