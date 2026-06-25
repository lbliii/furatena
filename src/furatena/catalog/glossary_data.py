"""Load glossary term data from data/."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = "data/glossary.yaml"


def repo_root_for_content(content_root: Path) -> Path:
    """Resolve the project root that owns ``data/glossary.yaml``."""
    resolved = content_root.resolve()
    if resolved.name == "chirp" and resolved.parent.name == "content":
        return resolved.parent.parent
    if resolved.name == "content" and resolved.parent.name == "site":
        return resolved.parent
    return resolved.parent


def glossary_path(site_root: Path, source: str = DEFAULT_PATH) -> Path:
    return site_root / source


def load_glossary_terms(site_root: Path, source: str = DEFAULT_PATH) -> list[dict[str, Any]]:
    path = glossary_path(site_root, source)
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    terms = data.get("terms", [])
    if not isinstance(terms, list):
        return []
    return [t for t in terms if isinstance(t, dict)]


def filter_terms(
    terms: list[dict[str, Any]],
    tags: list[str],
) -> list[dict[str, Any]]:
    if not tags:
        return []
    tag_set = {t.lower() for t in tags}
    matched: list[dict[str, Any]] = []
    for item in terms:
        item_tags = item.get("tags") or []
        if not isinstance(item_tags, list):
            continue
        normalized = {str(t).lower() for t in item_tags}
        if normalized & tag_set:
            matched.append(item)
    return matched


def lookup_term(
    site_root: Path,
    name: str,
    *,
    source: str = DEFAULT_PATH,
) -> dict[str, Any] | None:
    """Return the glossary entry whose term matches ``name`` (case-insensitive)."""
    needle = name.strip().casefold()
    if not needle:
        return None
    for item in load_glossary_terms(site_root, source):
        term = str(item.get("term", "")).strip()
        if term.casefold() == needle:
            return item
    return None
