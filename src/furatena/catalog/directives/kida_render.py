"""Render directive partials through Kida + chirp-ui macros."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from kida import ChoiceLoader, FileSystemLoader
from kida.template import Markup

from furatena.catalog.directives.icons import render_icon_html
from furatena.catalog.safe_html import trusted_renderer_fragment

DOCS_TEMPLATES = Path(__file__).resolve().parents[1] / "_templates"


@lru_cache(maxsize=1)
def _environment():
    import chirp_ui
    from chirp_ui.preview_env import make_preview_env

    chirpui_templates = Path(chirp_ui.__file__).resolve().parent / "templates"
    env = make_preview_env()
    env.loader = ChoiceLoader(
        [
            FileSystemLoader(str(DOCS_TEMPLATES)),
            FileSystemLoader(str(chirpui_templates)),
        ]
    )
    return env


def clear_directive_cache() -> None:
    """Drop cached Kida env (after template or catalog reload in dev)."""
    _environment.cache_clear()


def render_directive(template: str, /, **context: object) -> str:
    """Render a template under catalog/_templates/directives/."""
    env = _environment()
    tmpl = env.get_template(f"directives/{template}.html")
    return tmpl.render(**context)


def trusted_renderer_html(html: str) -> Markup:
    """Mark Patitas/highlighter child output for nested directive templates."""
    return trusted_renderer_fragment(html)


def render_doc_tabs(
    items: list[tuple[str, str, str, bool]],
    panels: list[str],
    *,
    sync_key: str = "",
    variant: str = "tabs",
    icons: list[str] | None = None,
) -> str:
    """Render Alpine + chirp-ui tab set (theme-skinned via directives.css)."""
    if not items:
        return ""
    initial = next((tab_id for tab_id, _, _, selected in items if selected), items[0][0])
    icon_list = icons or []
    tabs = [
        {
            "id": tab_id,
            "label": label,
            "badge": badge,
            "icon": render_icon_html(icon_list[index]) if index < len(icon_list) else "",
            "panel": trusted_renderer_html(panel),
            "disabled": False,
        }
        for index, ((tab_id, label, badge, _selected), panel) in enumerate(
            zip(items, panels, strict=True)
        )
    ]
    tab_ids = [tab["id"] for tab in tabs]
    tab_x_data = Markup(
        f"furaDocsTabSet({json.dumps(initial)}, {json.dumps(sync_key)}, {json.dumps(tab_ids)})"
    )
    return render_directive(
        "tabs",
        tabs=tabs,
        initial=initial,
        variant=variant,
        sync_key=sync_key,
        tab_x_data=tab_x_data,
    )
