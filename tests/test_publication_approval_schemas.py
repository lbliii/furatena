"""Publication approval v1 schema alignment."""

from __future__ import annotations

import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from furatena.catalog.audit_store import InMemoryAuditStore
from furatena.catalog.publication_approvals import (
    InMemoryPublicationApprovalStore,
    PublicationApprovalService,
)
from furatena.catalog.publication_contracts import (
    PublicationActor,
    PublicationDecisionKind,
)
from tests.publication_support import sample_plan


def _schemas() -> tuple[Registry, dict[str, dict[str, object]]]:
    catalog = files("furatena.catalog").joinpath("schemas")
    approval_root = catalog.joinpath("publication-approval/v1")
    publication_root = catalog.joinpath("publication/v1")
    documents = {
        "record": json.loads(
            approval_root.joinpath("record.schema.json").read_text(encoding="utf-8")
        ),
        "evaluation": json.loads(
            approval_root.joinpath("evaluation.schema.json").read_text(encoding="utf-8")
        ),
        "decision": json.loads(
            publication_root.joinpath("decision.schema.json").read_text(encoding="utf-8")
        ),
        "common": json.loads(
            publication_root.joinpath("common.schema.json").read_text(encoding="utf-8")
        ),
    }
    registry = Registry().with_resources(
        (str(document["$id"]), Resource.from_contents(document)) for document in documents.values()
    )
    return registry, documents


def test_approval_record_and_evaluation_validate_against_v1_schemas() -> None:
    registry, schemas = _schemas()
    service = _service()
    plan = sample_plan(required_count=0)
    record = service.decide(
        plan,
        actor=_actor(),
        decision=PublicationDecisionKind.WAIVE_WARNING,
        idempotency_key="waive-1",
        reason="Known warning.",
        diagnostic_ids=plan.validation.waivable_warning_ids,
        expires_at="2031-01-01T00:00:00Z",
    )
    evaluation = service.evaluate(plan, now="2030-01-01T00:00:00Z")

    Draft202012Validator(
        schemas["record"], registry=registry, format_checker=FormatChecker()
    ).validate(record.to_dict())
    Draft202012Validator(
        schemas["evaluation"], registry=registry, format_checker=FormatChecker()
    ).validate(evaluation.to_dict())


@pytest.mark.parametrize(
    ("schema_name", "mutation"),
    [
        ("record", {"input_digest": "not-a-digest"}),
        ("evaluation", {"required_count": -1}),
        ("evaluation", {"unexpected": True}),
    ],
)
def test_approval_schemas_reject_incompatible_records(
    schema_name: str, mutation: dict[str, object]
) -> None:
    registry, schemas = _schemas()
    service = _service()
    plan = sample_plan(required_count=0)
    record = service.decide(
        plan,
        actor=_actor(),
        decision=PublicationDecisionKind.WAIVE_WARNING,
        idempotency_key="waive-1",
        reason="Known warning.",
        diagnostic_ids=plan.validation.waivable_warning_ids,
        expires_at="2031-01-01T00:00:00Z",
    )
    original = (
        record.to_dict()
        if schema_name == "record"
        else service.evaluate(plan, now="2030-01-01T00:00:00Z").to_dict()
    )
    with pytest.raises(ValidationError):
        Draft202012Validator(
            schemas[schema_name], registry=registry, format_checker=FormatChecker()
        ).validate({**original, **mutation})


def _actor() -> PublicationActor:
    return PublicationActor(
        actor="reviewer@example.com",
        identity_source="test-oidc",
        roles=("publisher",),
        teams=("docs",),
    )


def _service() -> PublicationApprovalService:
    return PublicationApprovalService(
        store=InMemoryPublicationApprovalStore(),
        audit_store=InMemoryAuditStore(clock=lambda: 1_893_456_000.0),
        authorizer=lambda _plan, _actor, _decision: True,
        clock=lambda: "2030-01-01T00:00:00Z",
    )
