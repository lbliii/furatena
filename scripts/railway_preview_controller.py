#!/usr/bin/env python3
# ruff: noqa: E402,I001
"""Configure and verify one governed Railway pull-request environment."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.railway_preview_controller import main


if __name__ == "__main__":
    raise SystemExit(main())
