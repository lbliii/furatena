"""Draft 2020-12 publication schemas remain aligned with Python records."""

from __future__ import annotations

import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from furatena.catalog.publication_contracts import (
    PublicationDecision,
    PublicationDecisionKind,
    PublicationEvent,
    PublicationState,
    PublicationStateSnapshot,
)
from tests.publication_support import sample_actor, sample_plan


def _schemas() -> tuple[Registry, dict[str, dict[str, object]]]:
    root = files("furatena.catalog").joinpath("schemas/publication/v1")
    documents = {
        name: json.loads(root.joinpath(name).read_text(encoding="utf-8"))
        for name in (
            "common.schema.json",
            "plan.schema.json",
            "decision.schema.json",
            "event.schema.json",
            "state.schema.json",
        )
    }
    registry = Registry().with_resources(
        (str(document["$id"]), Resource.from_contents(document)) for document in documents.values()
    )
    return registry, documents


def _publication_records() -> dict[str, dict[str, object]]:
    plan = sample_plan()
    actor = sample_actor("reviewer@example.com")
    decision = PublicationDecision.create(
        plan_digest=plan.plan_digest,
        policy_digest=plan.bindings.policy_digest,
        actor=actor,
        decision=PublicationDecisionKind.APPROVE,
        timestamp="2030-01-01T00:00:00Z",
    )
    event = PublicationEvent.create(
        plan_digest=plan.plan_digest,
        state_version=2,
        from_state=PublicationState.PROPOSED,
        to_state=PublicationState.VALIDATING,
        event_type="validation.started",
        actor=actor,
        correlation_id=plan.correlation_id,
        timestamp="2030-01-01T00:01:00Z",
    )
    snapshot = PublicationStateSnapshot.initial(plan, timestamp="2030-01-01T00:00:00Z")
    return {
        "plan.schema.json": plan.to_dict(),
        "decision.schema.json": decision.to_dict(),
        "event.schema.json": event.to_dict(),
        "state.schema.json": snapshot.to_dict(),
    }


def test_trusted_publication_records_validate_against_v1_schemas() -> None:
    registry, schemas = _schemas()

    for schema_name, record in _publication_records().items():
        validator = Draft202012Validator(
            schemas[schema_name],
            registry=registry,
            format_checker=FormatChecker(),
        )
        validator.validate(record)


@pytest.mark.parametrize(
    ("schema_name", "mutation"),
    [
        ("plan.schema.json", {"schema_version": 2}),
        ("decision.schema.json", {"unexpected": True}),
        ("event.schema.json", {"state_version": 0}),
        ("state.schema.json", {"state": "unknown"}),
    ],
)
def test_v1_schemas_reject_incompatible_or_malformed_records(
    schema_name: str,
    mutation: dict[str, object],
) -> None:
    registry, schemas = _schemas()
    record = {**_publication_records()[schema_name], **mutation}
    validator = Draft202012Validator(
        schemas[schema_name],
        registry=registry,
        format_checker=FormatChecker(),
    )

    with pytest.raises(ValidationError):
        validator.validate(record)
