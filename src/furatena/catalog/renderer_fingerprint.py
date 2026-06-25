"""Fingerprint catalog renderer + theme assets for freeze staleness checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from furatena.catalog.docs_core import load_docs_core
from furatena.catalog.paths import catalog_root
from furatena.catalog.theme_assets import docs_core_assets_root, packaged_theme_assets

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
    core = docs_core_assets_root(theme_id)
    if core is not None:
        return _file_sig(core / "css" / "style.css")
    packaged = packaged_theme_assets(theme_id, docs_root)
    if packaged is None:
        return "missing"
    css_dir, _fonts, _branding = packaged
    return _file_sig(css_dir / "style.css")


def renderer_fingerprint(
    docs_root: Path,
    *,
    theme_id: str = "chirp",
    skin_pack_root: Path | None = None,
) -> str:
    """Hash renderer templates, handlers, skin pack, docs-core, and app theme."""
    parts: list[str] = [_packaged_theme_sig(theme_id, docs_root)]
    core = load_docs_core(theme_id)
    if core is not None:
        parts.extend(_tree_sig(core.root))
    if skin_pack_root is not None:
        parts.extend(_tree_sig(skin_pack_root))
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


def renderer_is_stale(
    docs_root: Path,
    frozen_dir: Path,
    *,
    theme_id: str = "chirp",
    skin_pack_root: Path | None = None,
) -> bool:
    """True when live renderer differs from the frozen export."""
    stored = read_renderer_fingerprint(frozen_dir)
    if stored is None:
        return True
    return stored != renderer_fingerprint(
        docs_root,
        theme_id=theme_id,
        skin_pack_root=skin_pack_root,
    )
