"""Shared icon markup for directive templates — Furatena vendored SVG library."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from kida.template import Markup

_ICONS_DIR = Path(__file__).resolve().parents[1] / "theme" / "assets" / "icons"

_RE_WIDTH_HEIGHT = re.compile(r'\s+(width|height)="[^"]*"')
_RE_CLASS = re.compile(r'\s+class="[^"]*"')
_RE_SVG_TAG = re.compile(r"<svg\s")

# Semantic aliases used by docs content and directives.
ICON_MAP: dict[str, str] = {
    "arrow-right": "arrow-right",
    "arrow-left": "arrow-left",
    "arrow-up": "arrow-up",
    "arrow-down": "arrow-down",
    "chevron-right": "chevron-right",
    "chevron-left": "chevron-left",
    "chevron-up": "chevron-up",
    "chevron-down": "chevron-down",
    "external": "external",
    "languages": "translate",
    "python": "file-py",
    "robot": "sparkle",
    "stethoscope": "heart",
    "arrows": "arrow-clockwise",
    "link": "link",
    "search": "magnifying-glass",
    "menu": "list",
    "close": "x",
    "x-mark": "x",
    "info": "info",
    "warning": "warning",
    "alert": "warning",
    "error": "x-circle",
    "check": "check",
    "success": "check-circle",
    "question": "question",
    "help": "question",
    "file": "file",
    "file-text": "file-text",
    "folder": "folder",
    "document": "file",
    "code": "code",
    "copy": "copy",
    "edit": "pencil",
    "trash": "trash",
    "download": "download",
    "upload": "upload",
    "settings": "settings",
    "star": "star",
    "heart": "heart",
    "bookmark": "bookmark",
    "tag": "tag",
    "calendar": "calendar",
    "clock": "clock",
    "pin": "map-pin",
    "user": "user",
    "arrow-clockwise": "arrow-clockwise",
    "sun": "sun",
    "moon": "moon",
    "palette": "palette",
    "tip": "tip",
    "note": "note",
    "example": "example",
    "danger": "danger",
    "caution": "caution",
    "lightbulb": "tip",
    "terminal": "terminal",
    "docs": "file-text",
    "notepad": "note",
    "atomic": "atomic",
    "starburst": "starburst",
    "boomerang": "boomerang",
    "alert-triangle": "warning",
    "alert-circle": "warning",
    "variable": "code",
    "arrows-angle-contract": "enlarge",
    "help-circle": "question",
    "file-plus": "file",
    "refresh-cw": "arrow-clockwise",
    "type": "code",
    "file-x": "error",
    "spell-check": "pencil",
    "plug": "puzzle-piece",
    "activity": "timer",
    "...": "filter",
}


def _escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


@lru_cache(maxsize=512)
def _load_icon_svg(name: str) -> str | None:
    path = _ICONS_DIR / f"{name}.svg"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def render_icon_html(icon_name: str, *, size: int = 18) -> Markup:
    """Inline SVG icon markup from the vendored Furatena icon set."""
    if not icon_name or re.fullmatch(r"[a-z0-9][a-z0-9-]*", icon_name) is None:
        return Markup("")
    mapped = ICON_MAP.get(icon_name, icon_name)
    svg_content = _load_icon_svg(mapped)
    if not svg_content:
        return Markup("")
    classes = ["fura-icon", f"icon-{icon_name}"]
    class_attr = " ".join(classes)
    svg_modified = _RE_WIDTH_HEIGHT.sub("", svg_content)
    svg_modified = _RE_CLASS.sub("", svg_modified)
    svg_modified = _RE_SVG_TAG.sub(
        f'<svg width="{size}" height="{size}" class="{class_attr}" aria-hidden="true" ',
        svg_modified,
        count=1,
    )
    return Markup(svg_modified)
