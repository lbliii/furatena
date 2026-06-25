"""Link and htmx boost validation for docs content and templates."""

from __future__ import annotations

import re
from pathlib import Path

from furatena.catalog.graph import normalize_internal_url
from furatena.catalog.links import _OPENING_A_RE, boost_internal_links

_DOCS_DIRECTIVES = Path(__file__).resolve().parent / "_templates" / "directives"
_LITERAL_A_TAG_RE = re.compile(r"<a\s+([^>]*?)>", re.IGNORECASE | re.DOTALL)
_HREF_ATTR_RE = re.compile(r"""href\s*=\s*["'](/[^"'#]+)["']""", re.IGNORECASE)


def shell_link_attrs(href: str) -> dict[str, object]:
    """htmx shell attrs used by ``DocsApp._route_link_attrs`` for internal paths."""
    if isinstance(href, str) and href.startswith("/") and not href.startswith("//"):
        return {
            "hx-boost": "true",
            "hx-target": "#main",
            "hx-swap": "innerHTML",
            "hx-select": "#page-root",
            "hx-sync": "#main:replace",
        }
    return {}


def check_body_link_boost(
    catalog,
    *,
    strict: bool = False,
) -> tuple[list[str], list[str]]:
    """Verify indexed body HTML internal links survive ``boost_doc_links``."""
    from furatena.catalog.check import should_validate_catalog_link

    errors: list[str] = []
    warnings: list[str] = []
    for node in catalog.nodes:
        if node.meta.get("draft") or not node.body_html:
            continue
        boosted = str(boost_internal_links(node.body_html, shell_link_attrs))
        for match in _OPENING_A_RE.finditer(boosted):
            href = match.group(1)
            tag = match.group(0)
            if "hx-boost=\"false\"" in tag or "data-hx-boost=\"false\"" in tag:
                continue
            normalized = normalize_internal_url(href)
            if normalized is None or not should_validate_catalog_link(normalized, catalog):
                continue
            if "hx-boost" in tag:
                continue
            location = node.source_path or node.slug or node.url
            message = f"{location}: internal link [{href}] missing hx-boost after boost_doc_links"
            if strict:
                errors.append(message)
            else:
                warnings.append(message)
    return sorted(errors), sorted(warnings)


def check_directive_template_hrefs(*, strict: bool = False) -> tuple[list[str], list[str]]:
    """Audit directive partials for static internal ``href`` without ``hx-boost``."""
    errors: list[str] = []
    warnings: list[str] = []
    if not _DOCS_DIRECTIVES.is_dir():
        return errors, warnings
    for path in sorted(_DOCS_DIRECTIVES.glob("*.html")):
        source = path.read_text(encoding="utf-8")
        if "{{" in source:
            continue
        rel = path.name
        for match in _LITERAL_A_TAG_RE.finditer(source):
            attrs = match.group(1)
            href_match = _HREF_ATTR_RE.search(attrs)
            if href_match is None:
                continue
            href = href_match.group(1)
            if normalize_internal_url(href) is None:
                continue
            if "hx-boost" in attrs or "data-hx-boost" in attrs:
                continue
            message = (
                f"directives/{rel}: literal internal href {href} without hx-boost "
                "(parent must pipe doc body through boost_doc_links)"
            )
            if strict:
                errors.append(message)
            else:
                warnings.append(message)
    return sorted(errors), sorted(warnings)
