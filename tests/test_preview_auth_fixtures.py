"""Shipped preview-auth examples remain canonical and schema-valid."""

from __future__ import annotations

import json
from importlib.resources import files

from furatena.catalog.preview_auth_contracts import PreviewGrantClaims, canonical_preview_auth_json
from tests.preview_auth_support import SAMPLES, grant_claims


def test_shipped_fixtures_are_exact_canonical_typed_messages() -> None:
    root = files("furatena.catalog").joinpath("fixtures/preview-auth/v1")
    for name, factory in SAMPLES.items():
        raw = root.joinpath(f"{name}.json").read_bytes()
        assert raw.endswith(b"\n")
        payload = json.loads(raw)
        loaded = type(factory()).from_dict(payload)
        assert loaded == factory()
        assert raw == canonical_preview_auth_json(loaded) + b"\n"

    claims_raw = root.joinpath("grant-claims.json").read_bytes()
    claims = PreviewGrantClaims.from_dict(json.loads(claims_raw))
    assert claims == grant_claims()
    assert claims_raw == canonical_preview_auth_json(claims) + b"\n"
