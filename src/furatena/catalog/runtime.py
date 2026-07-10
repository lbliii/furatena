"""Serve-mode resolution — author, hybrid, preview."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

from furatena.catalog.paths import catalog_root
from furatena.catalog.renderer_fingerprint import read_renderer_fingerprint, renderer_fingerprint


class ServeMode(enum.Enum):
    """Runtime posture for the docs app."""

    AUTHOR = "author"  # live index + watch
    HYBRID = "hybrid"  # frozen baseline + dirty overlay
    PREVIEW = "preview"  # frozen only, prod-like


@dataclass(frozen=True, slots=True)
class ServeConfig:
    mode: ServeMode
    frozen_dir: Path | None
    lazy_html: bool
    auto_reload: bool
    warn_stale_freeze: bool = False


def _newest_mtime(paths: tuple[Path, ...]) -> float:
    newest = 0.0
    skip = {".git", ".venv", "venv", "node_modules", "__pycache__", ".docs-cache"}
    for root in paths:
        if not root.is_dir():
            continue
        root = root.resolve()
        skip_frozen_under = root.name != "frozen"
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            parts = path.parts
            if any(part in skip for part in parts):
                continue
            if skip_frozen_under and "frozen" in parts:
                continue
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    return newest


def renderer_is_stale(docs_root: Path, frozen_dir: Path, *, theme_id: str = "chirp") -> bool:
    """True when live renderer differs from the frozen export."""
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.theme_pack import load_theme_pack

    docs_yaml = docs_root / "docs.yaml"
    skin_pack_root = None
    if docs_yaml.is_file():
        docs = load_docs_config(docs_yaml)
        theme_id = docs.theme.id
        if docs.theme.use:
            try:
                skin_pack_root = load_theme_pack(docs.theme.use).root
            except LookupError, TypeError, ValueError:
                skin_pack_root = None

    stored = read_renderer_fingerprint(frozen_dir)
    if stored is None:
        renderer_mtime = _newest_mtime((catalog_root(), docs_root / "theme"))
        freeze_mtime = _newest_mtime((frozen_dir,))
        return renderer_mtime > freeze_mtime + 1.0
    return stored != renderer_fingerprint(
        docs_root,
        theme_id=theme_id,
        skin_pack_root=skin_pack_root,
    )


def resolve_serve_config(
    *,
    docs_root: Path,
    content_roots: tuple[Path, ...],
    mode: ServeMode | None = None,
    frozen_dir: Path | None = None,
    env_frozen: bool = False,
) -> ServeConfig:
    """Pick author / hybrid / preview from flags and filesystem state."""
    frozen = frozen_dir or (docs_root / "frozen")
    has_frozen = frozen.is_dir() and (frozen / "catalog.json").is_file()

    if mode == ServeMode.AUTHOR:
        return ServeConfig(ServeMode.AUTHOR, None, False, True)

    if mode == ServeMode.PREVIEW or env_frozen:
        return ServeConfig(
            ServeMode.PREVIEW,
            frozen if has_frozen else None,
            True,
            False,
        )

    if not has_frozen:
        return ServeConfig(ServeMode.AUTHOR, None, False, True)

    freeze_mtime = _newest_mtime((frozen,))
    source_mtime = _newest_mtime(content_roots)
    content_stale = source_mtime > freeze_mtime + 1.0
    renderer_stale = renderer_is_stale(docs_root, frozen)
    if content_stale or renderer_stale:
        return ServeConfig(
            ServeMode.AUTHOR,
            None,
            False,
            True,
            warn_stale_freeze=content_stale,
        )
    return ServeConfig(
        ServeMode.HYBRID,
        frozen,
        True,
        True,
        warn_stale_freeze=content_stale,
    )
