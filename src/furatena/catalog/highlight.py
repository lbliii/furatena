"""Syntax-highlight code blocks via Patitas/Rosettes."""

from __future__ import annotations

from functools import lru_cache


from furatena.catalog.code_blocks import wrap_highlighted_html


@lru_cache(maxsize=1)
def _markdown():
    from patitas import Markdown

    return Markdown(plugins=[], highlight=True)


def highlight_code_block(language: str, code: str) -> str:
    """Render a fenced code block to highlighted HTML with theme chrome."""
    lang = language.strip() or "text"
    source = f"```{lang}\n{code.rstrip()}\n```"
    return wrap_highlighted_html(str(_markdown()(source)).strip())
