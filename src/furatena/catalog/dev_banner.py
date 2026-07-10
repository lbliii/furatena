"""Dev-server startup messaging — serve mode and reload layers."""

from __future__ import annotations

from furatena.catalog.runtime import ServeConfig, ServeMode


def format_serve_startup(
    serve: ServeConfig,
    *,
    page_count: int,
    mount_count: int,
    url: str,
) -> tuple[str, ...]:
    """Lines to print when starting the docs dev server."""
    summary = f"Mode: {serve.mode.value} · {page_count} pages across {mount_count} mounts"
    if serve.auto_reload and serve.mode != ServeMode.PREVIEW:
        summary = f"{summary} · reload: content (htmx) · theme (browser) · python (restart)"
    lines = [summary, f"Open {url}"]
    if serve.mode == ServeMode.PREVIEW:
        lines[0] = (
            f"Mode: preview · {page_count} pages across {mount_count} mounts · no live reload"
        )
    return tuple(lines)
