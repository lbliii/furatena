"""Diagram fences — mermaid blocks for client-side rendering."""

from __future__ import annotations

import html
import re

_MERMAID_FENCE = re.compile(
    r'<pre><code class="language-mermaid">([\s\S]*?)</code></pre>\s*',
    re.IGNORECASE,
)


def _escape_mermaid_text(source: str) -> str:
    """Escape diagram source for HTML text nodes without breaking ``-->`` arrows."""
    return source.replace("&", "&amp;").replace("<", "&lt;")


def mermaid_fence_html(source: str) -> str:
    """Wrap diagram source in the markup expected by ``fura-mermaid.js``."""
    body = _escape_mermaid_text(source.strip())
    return f'<div class="mermaid-wrapper"><div class="mermaid">{body}</div></div>\n'


def transform_mermaid_fences(html_doc: str) -> str:
    """Replace ``language-mermaid`` code fences with ``div.mermaid`` hooks."""

    def _replace(match: re.Match[str]) -> str:
        source = html.unescape(match.group(1))
        return mermaid_fence_html(source)

    if not html_doc or "language-mermaid" not in html_doc:
        return html_doc
    return _MERMAID_FENCE.sub(_replace, html_doc)
