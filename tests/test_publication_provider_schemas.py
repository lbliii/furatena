"""Publication provider v1 schemas remain aligned with trusted records."""

from __future__ import annotations

import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from furatena.catalog.publication_provider import (
    ProviderOutcome,
    PublicationChangeBundle,
    PublicationChangeResult,
    PublicationProfile,
)
from tests.provider_support import (
    sample_inspection,
    sample_profile,
    sample_provider_request,
)
from tests.publication_support import sample_plan


def _schemas() -> tuple[Registry, dict[str, dict[str, object]]]:
    root = files("furatena.catalog").joinpath("schemas/publication-provider/v1")
    documents = {
        name: json.loads(root.joinpath(name).read_text(encoding="utf-8"))
        for name in (
            "common.schema.json",
            "profile.schema.json",
            "inspection.schema.json",
            "request.schema.json",
            "result.schema.json",
            "bundle.schema.json",
        )
    }
    registry = Registry().with_resources(
        (str(document["$id"]), Resource.from_contents(document)) for document in documents.values()
    )
    return registry, documents


def _records() -> dict[str, dict[str, object]]:
    profile = sample_profile(PublicationProfile.PULL_REQUEST)
    request = sample_provider_request()
    result = PublicationChangeResult.create(
        request,
        outcome=ProviderOutcome.PREPARED,
        observed_base_source_revision=request.base_source_revision,
        resulting_source_revision=request.resulting_source_revision,
        changed_paths=request.approved_paths,
        observed_at="2030-01-01T00:01:00Z",
    )
    bundle = PublicationChangeBundle.create(
        sample_plan(),
        sample_profile(PublicationProfile.EXTERNAL),
        created_at="2030-01-01T00:00:00Z",
    )
    return {
        "profile.schema.json": profile.to_dict(),
        "inspection.schema.json": sample_inspection().to_dict(),
        "request.schema.json": request.to_dict(),
        "result.schema.json": result.to_dict(),
        "bundle.schema.json": bundle.to_dict(),
    }


def test_provider_records_validate_against_v1_schemas() -> None:
    registry, schemas = _schemas()

    for schema_name, record in _records().items():
        Draft202012Validator(
            schemas[schema_name],
            registry=registry,
            format_checker=FormatChecker(),
        ).validate(record)


@pytest.mark.parametrize(
    ("schema_name", "mutation"),
    [
        ("profile.schema.json", {"profile": "implicit-git"}),
        ("inspection.schema.json", {"permissions": ["force_push"]}),
        ("request.schema.json", {"schema_version": 2}),
        ("result.schema.json", {"unexpected": True}),
        ("bundle.schema.json", {"bundle_digest": "not-a-digest"}),
    ],
)
def test_provider_schemas_reject_incompatible_records(
    schema_name: str,
    mutation: dict[str, object],
) -> None:
    registry, schemas = _schemas()
    record = {**_records()[schema_name], **mutation}

    with pytest.raises(ValidationError):
        Draft202012Validator(
            schemas[schema_name],
            registry=registry,
            format_checker=FormatChecker(),
        ).validate(record)
