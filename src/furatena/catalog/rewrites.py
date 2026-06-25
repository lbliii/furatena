"""Configurable URL prefix rewrites (legacy deploy paths, etc.)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/chirp/docs/", "/docs/"),
    ("/chirp/", "/"),
)


@dataclass(frozen=True, slots=True)
class RewriteTable:
    """Longest-prefix rewrite rules for internal hrefs."""

    rules: tuple[tuple[str, str], ...] = _DEFAULT_PREFIXES

    def rewrite(self, href: str) -> str:
        href = href.strip()
        if not href.startswith("/"):
            return href
        best_from = ""
        best_to = ""
        for from_prefix, to_prefix in self.rules:
            if href.startswith(from_prefix) and len(from_prefix) > len(best_from):
                best_from = from_prefix
                best_to = to_prefix
        if not best_from:
            return href
        suffix = href[len(best_from) :]
        if not best_to.endswith("/") and suffix and not suffix.startswith("/"):
            return f"{best_to.rstrip('/')}/{suffix}"
        return best_to + suffix


def load_rewrite_table(path: Path | None) -> RewriteTable:
    """Load prefix rewrites from YAML or return legacy defaults."""
    if path is None or not path.is_file():
        return RewriteTable()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return load_rewrite_table_from_dict(raw)


_ACTIVE: RewriteTable | None = None


def get_rewrite_table() -> RewriteTable:
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = RewriteTable()
    return _ACTIVE


def set_rewrite_table(table: RewriteTable) -> None:
    global _ACTIVE
    _ACTIVE = table


def load_rewrite_table_from_dict(raw: dict[str, Any] | None) -> RewriteTable:
    if not raw:
        return RewriteTable()
    rules: list[tuple[str, str]] = []
    for item in raw.get("prefixes") or []:
        if not isinstance(item, dict):
            continue
        from_prefix = str(item.get("from") or "").strip()
        to_prefix = str(item.get("to") or "").strip()
        if from_prefix:
            rules.append((from_prefix, to_prefix))
    if not rules:
        return RewriteTable()
    rules.sort(key=lambda pair: len(pair[0]), reverse=True)
    return RewriteTable(rules=tuple(rules))
