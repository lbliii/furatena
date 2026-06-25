"""Lagoon — default Furatena docs skin (teal lagoon brand + docs chrome)."""

from __future__ import annotations

from pathlib import Path

from furatena.catalog.theme_pack import ThemePack

PACK = ThemePack(
    name="lagoon",
    root=Path(__file__).resolve().parent,
    packaged_id="furatena",
)
