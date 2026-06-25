"""Error page context and path-aware recovery helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from furatena.catalog.search_experience import hybrid_search_hits
from furatena.catalog.semantic import HybridHit

if TYPE_CHECKING:
    from chirp import Request

    from furatena.catalog.docs_app import DocsApp

_PATH_TOKEN_RE = re.compile(r"[-_/]+")

_ERROR_COPY: dict[int, dict[str, str]] = {
    403: {
        "error_title": "403 — Forbidden",
        "error_headline": "Access denied",
        "error_lead": "You don't have permission to view this resource.",
    },
    404: {
        "error_title": "404 — Page not found",
        "error_headline": "Page not found",
        "error_lead": "That URL is not in the catalog.",
    },
    405: {
        "error_title": "405 — Method not allowed",
        "error_headline": "Method not allowed",
        "error_lead": "This URL doesn't accept that HTTP method.",
    },
    413: {
        "error_title": "413 — Payload too large",
        "error_headline": "Request too large",
        "error_lead": "The request body exceeds the configured size limit.",
    },
    500: {
        "error_title": "500 — Something went wrong",
        "error_headline": "Something went wrong",
        "error_lead": "The server hit an unexpected error.",
    },
}

_RECOVERY_LINKS: tuple[dict[str, str], ...] = (
    {"label": "Home", "href": "/"},
    {"label": "Documentation", "href": "/docs/"},
    {"label": "Search", "href": "/search"},
    {"label": "Portal", "href": "/portal/"},
)

_SEARCH_STATUSES = frozenset({404})


def guess_search_query(path: str) -> str:
    """Turn a missing path into a catalog search query."""
    cleaned = path.strip("/")
    if not cleaned:
        return ""
    tokens = [part for part in _PATH_TOKEN_RE.split(cleaned) if part]
    if not tokens:
        return ""
    if len(tokens) >= 2:
        return " ".join(tokens[-2:])
    return tokens[-1]


def error_detail_message(exc: Exception | None, *, status: int) -> str:
    if exc is not None:
        detail = getattr(exc, "detail", "") or str(exc)
        detail = detail.strip()
        generic = {str(status), "Not Found", "Forbidden", "Method Not Allowed", "Payload Too Large"}
        if detail and detail not in generic and not detail.startswith(f"{status}:"):
            return detail
    return _ERROR_COPY.get(status, _ERROR_COPY[404])["error_lead"]


def error_allowed_methods(exc: Exception | None) -> tuple[str, ...]:
    """Parse ``Allow`` response header from a 405 exception."""
    if exc is None:
        return ()
    headers = getattr(exc, "headers", ())
    for name, value in headers:
        if str(name).lower() == "allow":
            return tuple(part.strip() for part in str(value).split(",") if part.strip())
    return ()


def split_recovery_hits(hits: tuple[HybridHit, ...] | list[HybridHit]) -> tuple[tuple[HybridHit, ...], tuple[HybridHit, ...]]:
    """Split hybrid hits into keyword and semantic-only suggestion groups."""
    keyword: list[HybridHit] = []
    semantic: list[HybridHit] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.node.node_id in seen:
            continue
        if hit.keyword_score > 0:
            keyword.append(hit)
            seen.add(hit.node.node_id)
        elif hit.semantic_score > 0:
            semantic.append(hit)
            seen.add(hit.node.node_id)
    return tuple(keyword), tuple(semantic)


def recovery_hits_for_query(app: DocsApp, query: str, *, limit: int = 6) -> tuple[tuple[HybridHit, ...], tuple[HybridHit, ...]]:
    """Keyword + semantic recovery hits for a free-text or path-derived query."""
    if not query:
        return (), ()
    result = hybrid_search_hits(
        app.catalog,
        app.embedding_index,
        query,
        limit=limit,
        lang=app.config.i18n.default_language if app.config.i18n.enabled else None,
    )
    return split_recovery_hits(result.hits)


def build_error_context(
    app: DocsApp,
    request: Request | None,
    *,
    status: int,
    exc: Exception | None = None,
) -> dict[str, Any]:
    """Template context for HTML error pages."""
    copy = _ERROR_COPY.get(status, _ERROR_COPY[404])
    path = request.path if request is not None else "/"
    query = guess_search_query(path) if status in _SEARCH_STATUSES else ""
    keyword_hits, semantic_hits = recovery_hits_for_query(app, query) if query else ((), ())

    ctx: dict[str, Any] = {
        **app._shell_context(request=request),  # noqa: SLF001
        "error_status": status,
        "error_title": copy["error_title"],
        "error_headline": copy["error_headline"],
        "error_lead": copy["error_lead"],
        "error_message": error_detail_message(exc, status=status),
        "error_path": path,
        "error_query": query,
        "error_keyword_hits": keyword_hits,
        "error_semantic_hits": semantic_hits,
        "error_suggestions": keyword_hits + semantic_hits,
        "hits": keyword_hits,
        "semantic_hits": semantic_hits,
        "error_allowed_methods": error_allowed_methods(exc),
        "error_recovery_links": _RECOVERY_LINKS,
        "error_show_search": status in _SEARCH_STATUSES,
        "app_surface": "page",
        "app_page_cls": f"chirp-theme-page fura-error-page fura-error-page--{status}",
        "node": None,
    }
    if request is not None:
        ctx["canonical_url"] = app._site_base(request) + path  # noqa: SLF001
    return ctx
