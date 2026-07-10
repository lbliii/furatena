"""Publication workflow command, receipt, and response schema tests."""

from __future__ import annotations

import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from furatena.catalog.publication_contracts import PublicationStateSnapshot
from furatena.catalog.publication_workflow import PublicationWorkflowResponse
from furatena.catalog.publication_workflow_store import PublicationOperationReceipt
from tests.publication_support import sample_plan


def _schemas() -> tuple[Registry, dict[str, dict[str, object]]]:
    root = files("furatena.catalog").joinpath("schemas/publication-workflow/v1")
    documents = {
        name: json.loads(root.joinpath(name).read_text(encoding="utf-8"))
        for name in ("command.schema.json", "receipt.schema.json", "response.schema.json")
    }
    registry = Registry().with_resources(
        (str(document["$id"]), Resource.from_contents(document)) for document in documents.values()
    )
    return registry, documents


def test_workflow_records_validate_against_v1_schemas() -> None:
    registry, schemas = _schemas()
    plan = sample_plan()
    receipt = PublicationOperationReceipt.start(
        idempotency_key="execute-1",
        command="execute",
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
        expected_state_version=4,
        input_payload={"plan_id": plan.plan_id},
        started_at="2030-01-01T00:00:00Z",
    )
    snapshot = PublicationStateSnapshot.initial(plan, timestamp="2030-01-01T00:00:00Z")
    records = {
        "command.schema.json": {
            "schema_version": 1,
            "command": "execute",
            "plan_id": plan.plan_id,
            "idempotency_key": "execute-1",
            "expected_state_version": 4,
        },
        "receipt.schema.json": receipt.to_dict(),
        "response.schema.json": PublicationWorkflowResponse(plan, snapshot, receipt).to_dict(),
    }

    for name, record in records.items():
        Draft202012Validator(
            schemas[name], registry=registry, format_checker=FormatChecker()
        ).validate(record)


@pytest.mark.parametrize(
    ("schema_name", "mutation"),
    [
        ("command.schema.json", {"command": "force_push"}),
        ("receipt.schema.json", {"input_digest": "not-a-digest"}),
        ("response.schema.json", {"unexpected": True}),
    ],
)
def test_workflow_schemas_reject_incompatible_records(
    schema_name: str, mutation: dict[str, object]
) -> None:
    registry, schemas = _schemas()
    plan = sample_plan()
    snapshot = PublicationStateSnapshot.initial(plan, timestamp="2030-01-01T00:00:00Z")
    receipt = PublicationOperationReceipt.start(
        idempotency_key="execute-1",
        command="execute",
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
        expected_state_version=4,
        input_payload={},
        started_at="2030-01-01T00:00:00Z",
    )
    originals = {
        "command.schema.json": {
            "schema_version": 1,
            "command": "execute",
            "plan_id": plan.plan_id,
            "idempotency_key": "execute-1",
            "expected_state_version": 4,
        },
        "receipt.schema.json": receipt.to_dict(),
        "response.schema.json": PublicationWorkflowResponse(plan, snapshot, receipt).to_dict(),
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(
            schemas[schema_name], registry=registry, format_checker=FormatChecker()
        ).validate({**originals[schema_name], **mutation})
