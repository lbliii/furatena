"""Draft 2020-12 preview schemas remain aligned with typed records."""

from __future__ import annotations

import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from furatena.catalog.preview_contracts import PreviewManifest
from tests.preview_support import sample_ready_manifest, sample_request


def _schemas() -> tuple[Registry, dict[str, dict[str, object]]]:
    root = files("furatena.catalog").joinpath("schemas/preview/v1")
    documents = {
        name: json.loads(root.joinpath(name).read_text(encoding="utf-8"))
        for name in ("common.schema.json", "request.schema.json", "manifest.schema.json")
    }
    registry = Registry().with_resources(
        (str(document["$id"]), Resource.from_contents(document)) for document in documents.values()
    )
    return registry, documents


def test_typed_preview_records_validate_against_v1_schemas() -> None:
    registry, schemas = _schemas()
    records = {
        "request.schema.json": sample_request().to_dict(),
        "manifest.schema.json": sample_ready_manifest().to_dict(),
    }

    for name, record in records.items():
        Draft202012Validator(
            schemas[name], registry=registry, format_checker=FormatChecker()
        ).validate(record)


@pytest.mark.parametrize(
    ("schema_name", "mutation"),
    [
        ("request.schema.json", {"schema_version": 2}),
        ("request.schema.json", {"unexpected": True}),
        ("manifest.schema.json", {"state": "promoted"}),
        ("manifest.schema.json", {"state_version": 0}),
    ],
)
def test_v1_schemas_reject_incompatible_or_malformed_records(
    schema_name: str,
    mutation: dict[str, object],
) -> None:
    registry, schemas = _schemas()
    base = (
        sample_request().to_dict()
        if schema_name == "request.schema.json"
        else sample_ready_manifest().to_dict()
    )
    validator = Draft202012Validator(
        schemas[schema_name], registry=registry, format_checker=FormatChecker()
    )

    with pytest.raises(ValidationError):
        validator.validate({**base, **mutation})


def test_schema_valid_record_round_trips_through_typed_manifest() -> None:
    manifest = sample_ready_manifest()

    assert PreviewManifest.from_dict(manifest.to_dict()).to_dict() == manifest.to_dict()
