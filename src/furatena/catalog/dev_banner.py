"""Dev-server startup messaging — serve mode and reload layers."""

from __future__ import annotations

import os
from pathlib import Path

from furatena.catalog.config import DocsConfig
from furatena.catalog.paths import catalog_root
from furatena.catalog.runtime import ServeConfig, ServeMode


def extra_reload_dirs(config: DocsConfig, repo_root: Path) -> tuple[Path, ...]:
    """Directories beyond theme assets that should trigger Pounce process reload."""
    dirs: list[Path] = [
        catalog_root(),
        config.framework_templates_dir,
    ]
    if os.environ.get("FURA_RELOAD_SRC", "").lower() in {"1", "true", "yes"}:
        dirs.append(repo_root / "src" / "furatena")
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in dirs:
        resolved = path.resolve()
        if resolved not in seen and resolved.is_dir():
            seen.add(resolved)
            unique.append(resolved)
    return tuple(unique)


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
        lines[0] = f"Mode: preview · {page_count} pages across {mount_count} mounts · no live reload"
    return tuple(lines)
