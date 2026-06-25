"""Shared helpers for directive HTML output."""

from __future__ import annotations

import re
from html import escape

GAP_CHIRPUI = {"small": "sm", "medium": "md", "large": "lg"}


def rewrite_href(href: str) -> str:
    """Rewrite legacy deploy prefixes via configured rules (Wave E)."""
    from furatena.catalog.rewrites import get_rewrite_table

    return get_rewrite_table().rewrite(href.strip())


def rewrite_doc_links(html: str) -> str:
    """Rewrite legacy /chirp/ link prefixes in rendered HTML."""

    def repl(match: re.Match[str]) -> str:
        return f'href="{rewrite_href(match.group(1))}"'

    return re.sub(r'href="(/chirp[^"]*)"', repl, html)


_CODE_PLACEHOLDER = "\x00CODE{index}\x00"


def render_inline_text(text: str) -> str:
    """Render directive titles and labels with basic inline markdown."""
    return _render_inline_markdown(text)


def render_inline_cell(cell_content: str) -> str:
    """Render list-table cell text with basic inline markdown."""
    if cell_content.strip() == "-":
        return '<span class="table-empty">—</span>'
    return _render_inline_markdown(cell_content)


def _render_inline_markdown(cell_content: str) -> str:
    code_spans: list[str] = []

    def stash_code(match: re.Match[str]) -> str:
        code_spans.append(match.group(1))
        return _CODE_PLACEHOLDER.format(index=len(code_spans) - 1)

    text = re.sub(r"`([^`]+)`", stash_code, cell_content)
    html = escape(text)
    html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", html)
    html = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"<em>\1</em>", html)

    def link_repl(match: re.Match[str]) -> str:
        href = rewrite_href(match.group(2))
        return f'<a href="{escape(href, quote=True)}">{match.group(1)}</a>'

    html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_repl, html)
    for index, code in enumerate(code_spans):
        html = html.replace(
            _CODE_PLACEHOLDER.format(index=index),
            f"<code>{escape(code)}</code>",
        )
    return html
