"""SEO helpers — JSON-LD, canonical URLs, Open Graph images."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from furatena.catalog.models import DocNode


def docs_base_url(request_host: str | None = None) -> str:
    """Resolve the public docs origin for canonical and OG URLs."""
    configured = os.environ.get("FURA_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    host = request_host or "127.0.0.1:8001"
    return f"http://{host}"


def canonical_url(base: str, path: str) -> str:
    """Build an absolute canonical URL from base origin and doc path."""
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{base.rstrip('/')}{normalized}"


def og_image_url(base: str, node: DocNode | None = None) -> str:
    """Return OG image URL — per-section default or site-wide fallback."""
    if node is not None and node.section:
        return f"{base.rstrip('/')}/og/{node.section}"
    return f"{base.rstrip('/')}/og/default"


def json_ld_article(*, node: DocNode, page_url: str, site_name: str = "Furatena") -> dict[str, Any]:
    """Build schema.org TechArticle JSON-LD for a doc page."""
    payload: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "TechArticle",
        "headline": node.title,
        "url": page_url,
        "isPartOf": {
            "@type": "WebSite",
            "name": site_name,
            "url": page_url.rsplit("/", max(1, node.url.strip("/").count("/") + 1))[0] + "/",
        },
    }
    description = (node.description or "").strip()
    if description:
        payload["description"] = description
    if node.tags:
        payload["keywords"] = sorted(node.tags)
    return payload


def json_ld_script(payload: dict[str, Any]) -> str:
    """Serialize JSON-LD for embedding in ``<script type=\"application/ld+json\">``."""
    return json.dumps(payload, indent=2, ensure_ascii=False)
