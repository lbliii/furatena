"""Theme asset bundling — one CSS request instead of dozens of @imports."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

_IMPORT_RE = re.compile(
    r"@import\s+url\(['\"]?([^'\")]+)['\"]?\)\s*;?",
    re.MULTILINE,
)


def bundle_css(entry: Path, *, cache_dir: Path) -> tuple[Path, str]:
    """Resolve ``@import url(...)`` recursively into one stylesheet."""
    entry = entry.resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    seen: set[Path] = set()
    chunks: list[str] = []

    def _collect(path: Path) -> None:
        path = path.resolve()
        if path in seen or not path.is_file():
            return
        seen.add(path)
        text = path.read_text(encoding="utf-8")
        pos = 0
        for match in _IMPORT_RE.finditer(text):
            chunks.append(text[pos : match.start()])
            rel = match.group(1).strip()
            if rel.startswith(("http://", "https://", "data:")):
                chunks.append(match.group(0))
            else:
                _collect((path.parent / rel).resolve())
            pos = match.end()
        chunks.append(text[pos:])

    _collect(entry)
    content = "\n".join(chunks)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    out = cache_dir / f"theme.{digest}.css"
    if not out.is_file() or out.read_text(encoding="utf-8") != content:
        out.write_text(content, encoding="utf-8")
    return out, digest


def copy_tree_files(src: Path, dest: Path, *, names: tuple[str, ...] | None = None) -> None:
    """Copy selected files from *src* into *dest* (flat, no subdirs)."""
    if not src.is_dir():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for path in src.iterdir():
        if not path.is_file():
            continue
        if names is not None and path.name not in names:
            continue
        target = dest / path.name
        if not target.is_file() or path.stat().st_mtime > target.stat().st_mtime:
            shutil.copy2(path, target)


def copy_fonts(fonts_dir: Path, dest: Path) -> None:
    """Copy theme font files into a static assets directory."""
    if not fonts_dir.is_dir():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for path in fonts_dir.rglob("*"):
        if path.is_file():
            target = dest / path.relative_to(fonts_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or path.stat().st_mtime > target.stat().st_mtime:
                shutil.copy2(path, target)


def write_assets_manifest(
    out_dir: Path,
    *,
    theme_href: str,
    fonts_prefix: str | None = None,
    branding_prefix: str | None = None,
) -> Path:
    """Write ``assets/manifest.json`` for preview/deploy serves."""
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "theme_css": theme_href,
        "fonts_prefix": fonts_prefix,
        "branding_prefix": branding_prefix,
    }
    path = assets_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def load_assets_manifest(frozen_dir: Path) -> dict | None:
    path = frozen_dir / "assets" / "manifest.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else None
