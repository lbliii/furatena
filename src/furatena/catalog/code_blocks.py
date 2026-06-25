"""Server-side code block chrome — chirp-theme wrapper + copy affordance."""

from __future__ import annotations

import re

from furatena.catalog.directives.kida_render import render_directive

# Rosettes blocks whose body is a bare <pre> (not yet wrapped).
_ROSETTES_BARE_PRE = re.compile(
    r'<div class="rosettes"(?P<attrs>[^>]*)>\s*(?P<body><pre[\s\S]*?</pre>)\s*</div>',
    re.IGNORECASE,
)
_PRE_BLOCK = re.compile(r"(<pre[\s\S]*?</pre>)", re.DOTALL)
_LANG_ATTR = re.compile(r"""data-language=["']([^"']+)["']""", re.IGNORECASE)


def _language_label(raw: str) -> str:
    return raw.strip().replace("-", " ").upper()


def wrap_code_block(body: str, *, language: str = "", rosettes: bool = False) -> str:
    """Wrap highlighted ``<pre>`` markup with theme code-block chrome."""
    if not body.strip() or "code-block-wrapper" in body:
        return body
    pre_match = _PRE_BLOCK.search(body)
    if not pre_match:
        return body
    return render_directive(
        "code_block",
        body=pre_match.group(1),
        language=_language_label(language),
        rosettes=rosettes,
    )


def wrap_highlighted_html(html: str) -> str:
    """Apply code-block wrappers to Rosettes output that is not yet wrapped."""
    if not html:
        return html

    def _replace(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        body = match.group("body")
        if "code-block-wrapper" in body:
            return match.group(0)
        lang_match = _LANG_ATTR.search(attrs)
        language = lang_match.group(1) if lang_match else ""
        wrapped = wrap_code_block(body, language=language, rosettes=True)
        return f'<div class="rosettes"{attrs}>{wrapped}</div>'

    return _ROSETTES_BARE_PRE.sub(_replace, html)


def wrap_doc_html(html: str) -> str:
    """Post-process rendered doc bodies with code-block chrome."""
    return wrap_highlighted_html(html)
