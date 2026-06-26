"""Shipped vendor assets for the docs runtime (htmx, extensions)."""

from __future__ import annotations

from importlib.resources import files

VENDOR_FILES = (
    "htmx.min.js",
    "htmx-ext-sse.js",
    "mermaid.min.js",
)


def vendor_dir() -> str:
    """Directory containing vendored JS shipped with ``furatena.catalog``."""
    return str(files("furatena.catalog") / "vendor")
