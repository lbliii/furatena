"""Draft 2020-12 preview-auth v1 schemas match typed records."""

from __future__ import annotations

import json
from dataclasses import replace
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from furatena.catalog.preview_auth_contracts import (
    PreviewAuthClientKind,
    PreviewAuthCodeKind,
    PreviewAuthError,
    PreviewAuthErrorCode,
    PreviewAuthorizationRequest,
)
from tests.preview_auth_support import (
    OPAQUE_A,
    SAMPLES,
    grant_exchange,
    jwks,
    one_time_code,
    signed_grant,
)

NAMES = (*SAMPLES, "grant-claims")


def _schemas() -> tuple[Registry, dict[str, dict[str, object]]]:
    root = files("furatena.catalog").joinpath("schemas/preview-auth/v1")
    names = ("common", *NAMES)
    documents = {
        name: json.loads(root.joinpath(f"{name}.schema.json").read_text()) for name in names
    }
    registry = Registry().with_resources(
        (str(document["$id"]), Resource.from_contents(document)) for document in documents.values()
    )
    return registry, documents


def test_every_schema_is_valid_and_accepts_its_typed_record() -> None:
    registry, schemas = _schemas()
    records = {name: factory().to_dict() for name, factory in SAMPLES.items()}
    records["grant-claims"] = signed_grant().claims.to_dict()
    for name, payload in records.items():
        Draft202012Validator.check_schema(schemas[name])
        Draft202012Validator(
            schemas[name], registry=registry, format_checker=FormatChecker()
        ).validate(payload)


@pytest.mark.parametrize("name", NAMES)
def test_schemas_reject_unknown_fields(name: str) -> None:
    registry, schemas = _schemas()
    payload = (
        signed_grant().claims.to_dict() if name == "grant-claims" else SAMPLES[name]().to_dict()
    )
    validator = Draft202012Validator(
        schemas[name], registry=registry, format_checker=FormatChecker()
    )
    with pytest.raises(ValidationError):
        validator.validate({**payload, "provider_native": {"kind": "forbidden"}})


def test_jwks_rejects_private_or_mismatched_key_material() -> None:
    registry, schemas = _schemas()
    payload = jwks().to_dict()
    validator = Draft202012Validator(
        schemas["jwks"], registry=registry, format_checker=FormatChecker()
    )
    keys = payload["keys"]
    assert isinstance(keys, list)
    key = keys[0]
    assert isinstance(key, dict)
    with pytest.raises(ValidationError):
        validator.validate({**payload, "keys": [{**key, "d": "private"}]})
    with pytest.raises(ValidationError):
        validator.validate({**payload, "keys": [{**key, "alg": "ES256"}]})


def test_device_code_and_exchange_validate_without_browser_redirect_fields() -> None:
    registry, schemas = _schemas()
    code = replace(
        one_time_code(),
        code_kind=PreviewAuthCodeKind.DEVICE_CODE,
        redirect_uri=None,
        user_code="ABCD-EFGH",
        verification_uri="https://auth.furatena.example/preview/device",
        poll_interval_seconds=5,
    )
    exchange = replace(
        grant_exchange(),
        client_kind=PreviewAuthClientKind.DEVICE,
        redirect_uri=None,
        code_verifier=None,
    )
    Draft202012Validator(schemas["one-time-code"], registry=registry).validate(code.to_dict())
    Draft202012Validator(schemas["grant-exchange"], registry=registry).validate(exchange.to_dict())


def test_error_schema_rejects_arbitrary_copy_that_could_echo_secrets() -> None:
    registry, schemas = _schemas()
    payload = SAMPLES["error"]().to_dict()
    validator = Draft202012Validator(schemas["error"], registry=registry)
    with pytest.raises(ValidationError):
        validator.validate({**payload, "safe_message": "Leaked one-time code value."})


def test_schemas_reject_expressible_noncanonical_and_unbounded_wire_values() -> None:
    registry, schemas = _schemas()
    cases = [
        (
            "authorization-request",
            {
                **SAMPLES["authorization-request"]().to_dict(),
                "requested_at": "2030-01-01T01:00:00+01:00",
            },
        ),
        (
            "authorization-request",
            {
                **SAMPLES["authorization-request"]().to_dict(),
                "redirect_uri": "https://pr-518.preview.example/callback?state=attacker",
            },
        ),
        (
            "authorization-request",
            {
                **SAMPLES["authorization-request"]().to_dict(),
                "binding": {
                    **SAMPLES["authorization-request"]().binding.to_dict(),
                    "origin": "https://exa%mple.example",
                },
            },
        ),
        (
            "registration",
            {
                **SAMPLES["registration"]().to_dict(),
                "redirect_paths": [f"/callback/{index}" for index in range(33)],
            },
        ),
        (
            "registration",
            {
                **SAMPLES["registration"]().to_dict(),
                "audiences": ["furatena.preview", "furatena.preview"],
            },
        ),
        (
            "grant-claims",
            {
                **signed_grant().claims.to_dict(),
                "scope": [f"preview:scope{index}" for index in range(33)],
            },
        ),
        (
            "jwks",
            {**jwks().to_dict(), "keys": [jwks().keys[0].to_dict()] * 33},
        ),
        (
            "jwks",
            {
                **jwks().to_dict(),
                "keys": [{**jwks().keys[0].to_dict(), "x": f"{jwks().keys[0].x[:-1]}x"}],
            },
        ),
        (
            "signed-grant",
            {
                **signed_grant().to_dict(),
                "compact": f"{signed_grant().compact[:-1]}z",
            },
        ),
    ]
    for name, payload in cases:
        with pytest.raises(ValidationError):
            Draft202012Validator(
                schemas[name], registry=registry, format_checker=FormatChecker()
            ).validate(payload)


def test_error_schema_requires_trusted_redirect_correlation_and_device_retry_timing() -> None:
    registry, schemas = _schemas()
    validator = Draft202012Validator(schemas["error"], registry=registry)
    unsafe = {
        **SAMPLES["error"]().to_dict(),
        "redirect_allowed": True,
        "state": "s" * 43,
    }
    with pytest.raises(ValidationError):
        validator.validate(unsafe)
    pending = PreviewAuthError(
        error=PreviewAuthErrorCode.AUTHORIZATION_PENDING,
        safe_message="Preview authorization is still pending.",
        remediation="Continue polling after retry_after_seconds.",
        retryable=True,
        request_id=OPAQUE_A,
        redirect_allowed=False,
        retry_after_seconds=5,
    ).to_dict()
    validator.validate(pending)
    with pytest.raises(ValidationError):
        validator.validate({**pending, "retry_after_seconds": None})


def test_cross_field_origin_binding_is_explicitly_a_semantic_model_check() -> None:
    registry, schemas = _schemas()
    payload = {
        **SAMPLES["authorization-request"]().to_dict(),
        "redirect_uri": "https://attacker.example/callback",
    }
    Draft202012Validator(
        schemas["authorization-request"], registry=registry, format_checker=FormatChecker()
    ).validate(payload)
    with pytest.raises(ValueError, match="bound preview origin"):
        PreviewAuthorizationRequest.from_dict(payload)
