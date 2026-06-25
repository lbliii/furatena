"""Docs-core bundle registry (``theme.id`` → installable CSS pack)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from furatena.catalog.theme_assets import docs_core_assets_root


@dataclass(frozen=True, slots=True)
class DocsCorePack:
    """Bundled ``.chirp-theme-*`` CSS — selected by ``theme.id`` in ``docs.yaml``."""

    id: str
    root: Path

    @property
    def css_dir(self) -> Path:
        return self.root / "css"

    @property
    def icons_dir(self) -> Path | None:
        icons = self.root / "icons"
        return icons if icons.is_dir() else None


def load_docs_core(theme_id: str) -> DocsCorePack | None:
    """Return the installed docs-core pack for *theme_id*, or ``None`` when unknown."""
    root = docs_core_assets_root(theme_id)
    if root is None:
        return None
    return DocsCorePack(id=theme_id, root=root)


def list_docs_core_ids() -> tuple[str, ...]:
    """Return built-in docs-core ids shipped with Furatena."""
    return ("furatena", "chirp")
