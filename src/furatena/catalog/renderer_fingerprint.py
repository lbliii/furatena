"""Fingerprint catalog renderer + theme assets for freeze staleness checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from furatena.catalog.theme_assets import packaged_theme_assets
from furatena.catalog.paths import catalog_root

_SKIP_PARTS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".docs-cache"})


def _file_sig(path: Path) -> str:
    if not path.is_file():
        return ""
    stat = path.stat()
    return f"{path}:{stat.st_mtime_ns}:{stat.st_size}"


def _tree_sig(root: Path) -> list[str]:
    parts: list[str] = []
    if not root.is_dir():
        return parts
    root = root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        parts.append(_file_sig(path))
    return parts


def _packaged_theme_sig(theme_id: str, docs_root: Path) -> str:
    if theme_id != "chirp":
        return theme_id
    packaged = packaged_theme_assets(theme_id, docs_root)
    if packaged is None:
        return "missing"
    css_dir, _fonts, _branding = packaged
    css_entry = css_dir / "style.css"
    return _file_sig(css_entry)


def renderer_fingerprint(docs_root: Path, *, theme_id: str = "chirp") -> str:
    """Hash renderer templates, handlers, local theme, and packaged skin entry."""
    parts: list[str] = [_packaged_theme_sig(theme_id, docs_root)]
    parts.extend(_tree_sig(catalog_root()))
    parts.extend(_tree_sig(docs_root / "theme"))
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def write_renderer_fingerprint(frozen_dir: Path, fingerprint: str) -> None:
    path = frozen_dir / "renderer.fingerprint"
    path.write_text(fingerprint + "\n", encoding="utf-8")
    manifest_path = frozen_dir / "assets" / "manifest.json"
    if manifest_path.is_file():
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw["renderer_fingerprint"] = fingerprint
            manifest_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def read_renderer_fingerprint(frozen_dir: Path) -> str | None:
    path = frozen_dir / "renderer.fingerprint"
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    manifest_path = frozen_dir / "assets" / "manifest.json"
    if manifest_path.is_file():
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            stored = raw.get("renderer_fingerprint")
            if isinstance(stored, str) and stored.strip():
                return stored.strip()
    return None


def renderer_is_stale(docs_root: Path, frozen_dir: Path, *, theme_id: str = "chirp") -> bool:
    """True when live renderer differs from the frozen export."""
    stored = read_renderer_fingerprint(frozen_dir)
    if stored is None:
        return True
    return stored != renderer_fingerprint(docs_root, theme_id=theme_id)
