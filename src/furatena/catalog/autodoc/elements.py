"""Autodoc element model — Chirp-native, no Bengal dependency."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DocElement:
    """Documented API symbol extracted from source."""

    name: str
    qualified_name: str
    description: str
    element_type: str
    source_file: Path | None = None
    line_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    children: list[DocElement] = field(default_factory=list)
