"""Directive partial registry for catalog index-time Kida render.

These templates are compiled when markdown directives render — not from
route handlers. ``app.py`` registers them for ``chirp check`` via
``TYPE_CHECKING`` ``Fragment(...)`` stubs that mirror this tuple.
"""

from __future__ import annotations

DIRECTIVE_TEMPLATES: tuple[str, ...] = (
    "directives/accordion.html",
    "directives/callout.html",
    "directives/card_grid.html",
    "directives/card_link.html",
    "directives/card_static.html",
    "directives/child_cards.html",
    "directives/code_block.html",
    "directives/figure.html",
    "directives/glossary.html",
    "directives/gist.html",
    "directives/literalinclude.html",
    "directives/related.html",
    "directives/step.html",
    "directives/steps.html",
    "directives/table.html",
    "directives/tabs.html",
    "directives/version_callout.html",
    "directives/youtube.html",
)
