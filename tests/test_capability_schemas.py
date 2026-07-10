"""Capability policy, request, and decision schemas track Python records."""

from __future__ import annotations

import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from furatena.catalog.capability_policy import (
    CAPABILITY_EVALUATOR,
    Capability,
    CapabilityEffect,
    CapabilityPolicy,
    CapabilityRequest,
    CapabilityRule,
    CapabilityScope,
    CapabilitySubject,
)


def _schemas() -> tuple[Registry, dict[str, dict[str, object]]]:
    root = files("furatena.catalog").joinpath("schemas/capability/v1")
    documents = {
        name: json.loads(root.joinpath(name).read_text(encoding="utf-8"))
        for name in (
            "common.schema.json",
            "policy.schema.json",
            "request.schema.json",
            "decision.schema.json",
        )
    }
    registry = Registry().with_resources(
        (str(document["$id"]), Resource.from_contents(document)) for document in documents.values()
    )
    return registry, documents


def _records() -> dict[str, dict[str, object]]:
    policy = CapabilityPolicy.create(
        policy_version="schema-v1",
        rules=(
            CapabilityRule(
                rule_id="reader",
                effect=CapabilityEffect.ALLOW,
                priority=1,
                capabilities=(Capability.READ,),
                roles=("reader",),
                tenants=("acme",),
            ),
        ),
    )
    request = CapabilityRequest(
        subject=CapabilitySubject(
            actor="reader@example.com",
            identity_source="test-oidc",
            identity_fingerprint="trusted-fingerprint",
            roles=("reader",),
            teams=(),
            tenant="acme",
            workspace="docs",
            site="developer",
            trusted=True,
        ),
        capability=Capability.READ,
        scope=CapabilityScope(
            tenant="acme",
            workspace="docs",
            site="developer",
            mount="product",
            path="docs/guide.md",
            lifecycle_state="public",
        ),
        correlation_id="schema-test",
        evaluated_at="2030-01-01T00:00:00Z",
    )
    decision = CAPABILITY_EVALUATOR.evaluate(policy, request)
    return {
        "policy.schema.json": policy.to_dict(),
        "request.schema.json": request.to_dict(),
        "decision.schema.json": decision.to_dict(),
    }


def test_capability_records_validate_against_v1_schemas() -> None:
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
        ("policy.schema.json", {"schema_version": 2}),
        ("request.schema.json", {"capability": "impersonate"}),
        ("decision.schema.json", {"unexpected": True}),
    ],
)
def test_capability_schemas_reject_incompatible_records(
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
