"""Shipped vendor assets for the docs runtime (htmx, extensions)."""

from __future__ import annotations

import os
from importlib.resources import files

HTMX_PREVIEW_ENV = "FURA_HTMX_PREVIEW"
HTMX4_PREVIEW_VERSION = "4.0.0-beta5"
HTMX4_PREVIEW_FILES = (
    f"htmx-{HTMX4_PREVIEW_VERSION}.min.js",
    f"htmx-2-compat-{HTMX4_PREVIEW_VERSION}.min.js",
    f"hx-sse-{HTMX4_PREVIEW_VERSION}.min.js",
    f"hx-preload-{HTMX4_PREVIEW_VERSION}.min.js",
)

VENDOR_FILES = (
    "htmx.min.js",
    "htmx-ext-sse.js",
    "htmx-ext-preload.js",
    *HTMX4_PREVIEW_FILES,
    "mermaid.min.js",
)


def vendor_dir() -> str:
    """Directory containing vendored JS shipped with ``furatena.catalog``."""
    return str(files("furatena.catalog") / "vendor")


def resolve_htmx_preview() -> str | None:
    """Resolve the explicitly pinned htmx preview, or keep the stable default."""
    configured = os.environ.get("FURA_HTMX_PREVIEW", "").strip()
    if not configured:
        return None
    if configured != HTMX4_PREVIEW_VERSION:
        raise ValueError(f"{HTMX_PREVIEW_ENV} must be {HTMX4_PREVIEW_VERSION!r} when enabled")
    return configured
