"""Committed preview fixtures remain canonical and parseable."""

from __future__ import annotations

import json
from pathlib import Path

from furatena.catalog.preview_contracts import (
    PreviewManifest,
    PreviewRequest,
    canonical_json_bytes,
)

FIXTURES = Path(__file__).parent / "fixtures" / "preview" / "v1"


def test_preview_fixtures_round_trip_without_contract_drift() -> None:
    records = {
        "create-request.json": PreviewRequest,
        "ready-manifest.json": PreviewManifest,
    }

    for name, record_type in records.items():
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        loaded = record_type.from_dict(payload)

        assert canonical_json_bytes(loaded.to_dict()) == canonical_json_bytes(payload)
