"""Provider-neutral preview authorization typed contracts."""

from __future__ import annotations

import base64
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from furatena.catalog.preview_auth_contracts import (
    PREVIEW_AUTH_SENSITIVITY,
    PreviewAuthBinding,
    PreviewAuthClientKind,
    PreviewAuthCodeKind,
    PreviewAuthConsumptionKind,
    PreviewAuthError,
    PreviewAuthErrorCode,
    PreviewAuthorizationRequest,
    PreviewGrantClaims,
    PreviewJwks,
    PreviewSignedGrant,
    canonical_preview_auth_json,
    canonical_preview_origin,
    pkce_s256_challenge,
    preview_auth_consumption_digest,
    redact_preview_auth,
    unsupported_version_error,
    verify_pkce_s256,
)
from tests.preview_auth_support import (
    OPAQUE_A,
    OPAQUE_B,
    SAMPLES,
    VERIFIER,
    authorization_request,
    binding,
    grant_claims,
    grant_exchange,
    jwks,
    one_time_code,
    registration,
    signed_grant,
)


def test_every_message_is_immutable_deterministic_and_round_trips() -> None:
    for factory in SAMPLES.values():
        record = factory()
        loaded = type(record).from_dict(record.to_dict())
        assert loaded == record
        assert canonical_preview_auth_json(loaded) == canonical_preview_auth_json(record.to_dict())
        with pytest.raises(FrozenInstanceError):
            record.__setattr__("schema_version", 2)

    claims = grant_claims()
    assert PreviewGrantClaims.from_dict(claims.to_dict()) == claims
    assert canonical_preview_auth_json(claims) == canonical_preview_auth_json(claims.to_dict())
    with pytest.raises(FrozenInstanceError):
        claims.__setattr__("expires_at", claims.expires_at + 1)


def test_origin_canonicalization_has_one_exact_https_spelling() -> None:
    assert (
        canonical_preview_origin("HTTPS://BÜCHER.Example.:443/") == "https://xn--bcher-kva.example"
    )
    assert canonical_preview_origin("https://faß.de") == "https://xn--fa-hia.de"
    assert canonical_preview_origin("https://example\uff0ecom") == "https://example.com"
    assert canonical_preview_origin("https://XN--FA-HIA.DE") == "https://xn--fa-hia.de"
    assert canonical_preview_origin("https://[2001:0db8::1]:8443") == "https://[2001:db8::1]:8443"
    for invalid in (
        "http://preview.example",
        "https://user:secret@preview.example",
        "https://preview.example/path",
        "https://preview.example?next=evil",
        "https://preview.example/#fragment",
        "https://exa%mple.example",
        "https://foo_bar.example",
        "https://xn--.example",
        "https://preview.example..",
        "https://127.01.01.01",
        "https://[fe80::1%25en0]",
        "https://ab\u200dcd.example",
    ):
        with pytest.raises(ValueError):
            canonical_preview_origin(invalid)


def test_browser_is_same_origin_pkce_s256_and_device_never_redirects() -> None:
    request = authorization_request()
    assert verify_pkce_s256(VERIFIER, request.code_challenge or "")
    assert not verify_pkce_s256(VERIFIER + "x", request.code_challenge or "")
    with pytest.raises(ValueError, match="bound preview origin"):
        replace(request, redirect_uri="https://lookalike.example/callback")
    with pytest.raises(ValueError, match="requires PKCE S256"):
        replace(request, code_challenge_method="plain")
    with pytest.raises(ValueError, match="must not contain a query"):
        replace(request, redirect_uri=f"{request.redirect_uri}?state=attacker")
    with pytest.raises(ValueError, match="device request forbids"):
        PreviewAuthorizationRequest(
            request_id=OPAQUE_A,
            client_kind=PreviewAuthClientKind.DEVICE,
            binding=binding(),
            audience="furatena.preview",
            nonce=OPAQUE_B,
            requested_at="2030-01-01T00:00:00Z",
            expires_at="2030-01-01T00:01:00Z",
            redirect_uri="https://pr-518.preview.example/callback",
        )
    device = replace(
        request,
        client_kind=PreviewAuthClientKind.DEVICE,
        redirect_uri=None,
        state=None,
        code_challenge=None,
        code_challenge_method=None,
    )
    assert device.redirect_uri is None

    device_code = replace(
        one_time_code(),
        code_kind=PreviewAuthCodeKind.DEVICE_CODE,
        redirect_uri=None,
        user_code="ABCD-EFGH",
        verification_uri="https://auth.furatena.example/preview/device",
        poll_interval_seconds=5,
    )
    assert device_code.user_code == "ABCD-EFGH"
    device_exchange = replace(
        grant_exchange(),
        client_kind=PreviewAuthClientKind.DEVICE,
        redirect_uri=None,
        code_verifier=None,
    )
    assert device_exchange.code_verifier is None


def test_binding_is_immutable_across_request_code_exchange_and_claims() -> None:
    expected = binding().to_dict()
    assert authorization_request().binding.to_dict() == expected
    assert one_time_code().binding.to_dict() == expected
    assert grant_exchange().binding.to_dict() == expected
    claims = grant_claims().to_dict()
    assert {
        key: claims[key] for key in ("repository_id", "pull_request_number", "head_sha", "origin")
    } == expected

    with pytest.raises(ValueError, match="head_sha"):
        PreviewAuthBinding("repository-4242", 518, "A" * 40, "https://pr-518.preview.example")


def test_compact_jws_is_structurally_parsed_but_not_treated_as_verified() -> None:
    grant = signed_grant()
    assert grant.key_id == "preview-key-2030-01"
    assert grant.claims == grant_claims()
    tampered = grant.compact.rsplit(".", 1)[0] + ".short"
    with pytest.raises(ValueError, match="signature"):
        replace(grant, compact=tampered)
    # Construction deliberately has no `verified` state or signature-verification method.
    assert not hasattr(grant, "verified")


def test_compact_jws_rejects_semantically_or_base64_noncanonical_segments() -> None:
    grant = signed_grant()
    header, payload, signature = grant.compact.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["scope"] = ["preview:z", "preview:a"]
    changed_payload = (
        base64.urlsafe_b64encode(canonical_preview_auth_json(claims)).rstrip(b"=").decode()
    )
    with pytest.raises(ValueError, match="canonical typed form"):
        PreviewSignedGrant(f"{header}.{changed_payload}.{signature}")
    with pytest.raises(ValueError, match="canonical base64url"):
        PreviewSignedGrant(f"{header}.{payload}.{signature[:-1]}z")
    with pytest.raises(ValueError, match="canonical base64url"):
        replace(jwks().keys[0], x=f"{jwks().keys[0].x[:-1]}x")


def test_grants_are_short_lived_and_all_security_bindings_are_required() -> None:
    claims = grant_claims()
    with pytest.raises(ValueError, match="exceeds"):
        replace(claims, expires_at=claims.issued_at + 601)
    with pytest.raises(ValueError, match="incompatible fields"):
        PreviewGrantClaims.from_dict({**claims.to_dict(), "provider": "railway"})


def test_jwks_is_public_only_and_stale_or_unknown_keys_fail_closed() -> None:
    document = jwks()
    assert document.keys[0].key_ops == ("verify",)
    with pytest.raises(ValueError, match="only verification"):
        replace(document.keys[0], key_ops=("sign",))
    with pytest.raises(ValueError, match="incompatible fields"):
        PreviewJwks.from_dict({**document.to_dict(), "private_key": "forbidden"})
    assert unsupported_version_error(2).redirect_allowed is False


def test_redaction_removes_secrets_and_hashes_correlatable_metadata() -> None:
    payload = grant_exchange().to_dict()
    redacted = redact_preview_auth(payload)
    rendered = str(redacted)
    for forbidden in (VERIFIER, "c" * 43, OPAQUE_B, "repository-4242", "a" * 40):
        assert forbidden not in rendered
    assert redacted["code"] == "[REDACTED]"
    assert PREVIEW_AUTH_SENSITIVITY["exchange.code_verifier"] == "secret"
    assert PREVIEW_AUTH_SENSITIVITY["authorization.audience"] == "confidential"
    registration_log = str(redact_preview_auth(registration().to_dict()))
    for confidential in (
        registration().issuer,
        registration().created_at,
        registration().jwks_uri,
    ):
        assert confidential not in registration_log
    assert str(redact_preview_auth(registration().to_dict())["audiences"]).startswith("sha256:")
    assert (
        redact_preview_auth({"unknown_future_secret": "do-not-log"})["unknown_future_secret"]
        == "[OMITTED]"
    )


def test_current_reader_rejects_incompatible_changes_with_actionable_version_error() -> None:
    for factory in SAMPLES.values():
        record = factory()
        payload = record.to_dict()
        with pytest.raises(ValueError, match=r"supported_versions=\[1\]"):
            type(record).from_dict({**payload, "schema_version": 2})
        with pytest.raises(ValueError, match="unknown required_future_field"):
            type(record).from_dict({**payload, "required_future_field": True})

    claims = grant_claims().to_dict()
    with pytest.raises(ValueError, match=r"supported_versions=\[1\]"):
        PreviewGrantClaims.from_dict({**claims, "ver": 2})
    with pytest.raises(ValueError, match="unknown required_future_field"):
        PreviewGrantClaims.from_dict({**claims, "required_future_field": True})
    diagnostic = unsupported_version_error(2, OPAQUE_A)
    assert diagnostic.supported_versions == (1,)
    assert "supported_versions" in diagnostic.remediation


def test_registration_rejects_cross_origin_broker_endpoints() -> None:
    record = registration()
    with pytest.raises(ValueError, match="issuer origin"):
        replace(record, jwks_uri="https://attacker.example/jwks.json")
    evolving = replace(record, supported_versions=(2, 1), deprecated_versions=(1,))
    assert evolving.supported_versions == (1, 2)
    with pytest.raises(ValueError, match="inconsistent version negotiation"):
        replace(record, supported_versions=(1,), deprecated_versions=(2,))


def test_core_messages_contain_no_provider_specific_objects() -> None:
    rendered = "".join(
        canonical_preview_auth_json(factory()).decode() for factory in SAMPLES.values()
    )
    assert "railway" not in rendered.lower()
    assert "github" not in rendered.lower()


def test_one_time_material_has_bounded_hash_only_storage_helpers() -> None:
    challenge = pkce_s256_challenge(VERIFIER)
    assert len(challenge) == 43
    digest = preview_auth_consumption_digest(
        PreviewAuthConsumptionKind.CODE,
        "c" * 43,
        issuer="https://auth.furatena.example/preview",
        binding=binding(),
        secret_key=b"k" * 32,
    )
    assert digest.startswith("hmac-sha256:") and "c" * 43 not in digest
    assert digest != preview_auth_consumption_digest(
        PreviewAuthConsumptionKind.CODE,
        "c" * 43,
        issuer="https://auth.furatena.example/preview",
        binding=replace(binding(), head_sha="b" * 40),
        secret_key=b"k" * 32,
    )
    user_code_digest = preview_auth_consumption_digest(
        PreviewAuthConsumptionKind.USER_CODE,
        "ABCD-EFGH",
        issuer="https://auth.furatena.example/preview",
        binding=binding(),
        secret_key=b"k" * 32,
    )
    assert "ABCD-EFGH" not in user_code_digest
    assert user_code_digest != preview_auth_consumption_digest(
        PreviewAuthConsumptionKind.USER_CODE,
        "ABCD-EFGH",
        issuer="https://auth.furatena.example/preview",
        binding=binding(),
        secret_key=b"m" * 32,
    )
    with pytest.raises(ValueError, match="at least 32 bytes"):
        preview_auth_consumption_digest(
            PreviewAuthConsumptionKind.USER_CODE,
            "ABCD-EFGH",
            issuer="https://auth.furatena.example/preview",
            binding=binding(),
            secret_key=b"short",
        )


def test_structured_errors_use_fixed_copy_that_cannot_echo_secrets() -> None:
    with pytest.raises(ValueError, match="fixed safe copy"):
        PreviewAuthError(
            error=PreviewAuthErrorCode.INVALID_GRANT,
            safe_message=f"Grant {'c' * 43} is invalid.",
            remediation="Try again.",
            retryable=False,
            request_id=None,
            redirect_allowed=False,
        )


def test_error_redirects_require_validated_state_and_device_polling_is_explicit() -> None:
    with pytest.raises(ValueError, match="must not redirect"):
        replace(SAMPLES["error"](), redirect_allowed=True, state="s" * 43)
    with pytest.raises(ValueError, match="requires its validated request_id"):
        PreviewAuthError(
            error=PreviewAuthErrorCode.INVALID_REQUEST,
            safe_message="The authorization request is invalid.",
            remediation="Restart authorization with a valid v1 request.",
            retryable=False,
            request_id=None,
            redirect_allowed=True,
            state="s" * 43,
        )
    redirectable = PreviewAuthError(
        error=PreviewAuthErrorCode.INVALID_REQUEST,
        safe_message="The authorization request is invalid.",
        remediation="Restart authorization with a valid v1 request.",
        retryable=False,
        request_id=OPAQUE_A,
        redirect_allowed=True,
        state="s" * 43,
    )
    assert redirectable.state == "s" * 43
    pending = PreviewAuthError(
        error=PreviewAuthErrorCode.AUTHORIZATION_PENDING,
        safe_message="Preview authorization is still pending.",
        remediation="Continue polling after retry_after_seconds.",
        retryable=True,
        request_id=OPAQUE_A,
        redirect_allowed=False,
        retry_after_seconds=5,
    )
    assert pending.retry_after_seconds == 5
    with pytest.raises(ValueError, match="requires retry_after_seconds"):
        replace(pending, retry_after_seconds=None)


def test_external_collection_bounds_are_enforced_before_sorting() -> None:
    with pytest.raises(ValueError, match="redirect_paths list exceeds"):
        replace(registration(), redirect_paths=tuple(f"/callback/{index}" for index in range(33)))
    with pytest.raises(ValueError, match="audiences list exceeds"):
        replace(registration(), audiences=tuple(f"preview.{index}" for index in range(17)))
    with pytest.raises(ValueError, match="scopes list exceeds"):
        replace(grant_claims(), scopes=tuple(f"preview:scope{index}" for index in range(33)))
    with pytest.raises(ValueError, match="JWKS exceeds"):
        replace(jwks(), keys=jwks().keys * 33)
    with pytest.raises(ValueError, match="audiences list contains duplicates"):
        replace(registration(), audiences=("furatena.preview", "furatena.preview"))
    with pytest.raises(ValueError, match="scopes list contains duplicates"):
        replace(grant_claims(), scopes=("preview:read", "preview:read"))
