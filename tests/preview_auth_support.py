"""Canonical sample records for preview-auth v1 contract tests."""

from __future__ import annotations

import base64

from furatena.catalog.preview_auth_contracts import (
    PreviewAuthAlgorithm,
    PreviewAuthBinding,
    PreviewAuthClientKind,
    PreviewAuthCodeKind,
    PreviewAuthError,
    PreviewAuthErrorCode,
    PreviewAuthorizationRequest,
    PreviewAuthRegistration,
    PreviewAuthRevocationReason,
    PreviewGrantClaims,
    PreviewGrantExchange,
    PreviewJwk,
    PreviewJwks,
    PreviewOneTimeCode,
    PreviewRevocationSignal,
    PreviewSignedGrant,
    canonical_preview_auth_json,
    pkce_s256_challenge,
)

OPAQUE_A = "a" * 43
OPAQUE_B = "b" * 43
VERIFIER = "verify-this-preview-code-with-pkce-s256-only-1234567890"


def binding() -> PreviewAuthBinding:
    return PreviewAuthBinding("repository-4242", 518, "a" * 40, "https://pr-518.preview.example")


def registration() -> PreviewAuthRegistration:
    return PreviewAuthRegistration(
        registration_id=OPAQUE_A,
        issuer="https://auth.furatena.example/preview",
        binding=binding(),
        authorization_endpoint="https://auth.furatena.example/preview/authorize",
        grant_endpoint="https://auth.furatena.example/preview/grants",
        jwks_uri="https://auth.furatena.example/preview/jwks.json",
        revocation_endpoint="https://auth.furatena.example/preview/revocations",
        redirect_paths=("/_fura/preview-auth/callback",),
        audiences=("furatena.preview",),
        created_at="2030-01-01T00:00:00Z",
        expires_at="2030-01-08T00:00:00Z",
    )


def authorization_request() -> PreviewAuthorizationRequest:
    return PreviewAuthorizationRequest(
        request_id=OPAQUE_A,
        client_kind=PreviewAuthClientKind.BROWSER,
        binding=binding(),
        audience="furatena.preview",
        nonce=OPAQUE_B,
        requested_at="2030-01-01T00:00:00Z",
        expires_at="2030-01-01T00:05:00Z",
        redirect_uri="https://pr-518.preview.example/_fura/preview-auth/callback",
        state="s" * 43,
        code_challenge=pkce_s256_challenge(VERIFIER),
        code_challenge_method="S256",
    )


def one_time_code() -> PreviewOneTimeCode:
    return PreviewOneTimeCode(
        code="c" * 43,
        code_kind=PreviewAuthCodeKind.AUTHORIZATION_CODE,
        binding=binding(),
        audience="furatena.preview",
        subject="reviewer:stable-pseudonym",
        nonce=OPAQUE_B,
        issued_at="2030-01-01T00:01:00Z",
        expires_at="2030-01-01T00:03:00Z",
        redirect_uri="https://pr-518.preview.example/_fura/preview-auth/callback",
    )


def grant_exchange() -> PreviewGrantExchange:
    return PreviewGrantExchange(
        exchange_id="e" * 43,
        client_kind=PreviewAuthClientKind.BROWSER,
        code="c" * 43,
        binding=binding(),
        audience="furatena.preview",
        nonce=OPAQUE_B,
        exchanged_at="2030-01-01T00:01:30Z",
        redirect_uri="https://pr-518.preview.example/_fura/preview-auth/callback",
        code_verifier=VERIFIER,
    )


def grant_claims() -> PreviewGrantClaims:
    return PreviewGrantClaims(
        issuer="https://auth.furatena.example/preview",
        audience="furatena.preview",
        subject="reviewer:stable-pseudonym",
        repository_id="repository-4242",
        pull_request_number=518,
        head_sha="a" * 40,
        origin="https://pr-518.preview.example",
        nonce=OPAQUE_B,
        key_id="preview-key-2030-01",
        jti="grant_jti_12345678901234567890",
        issued_at=1_893_456_090,
        not_before=1_893_456_090,
        expires_at=1_893_456_390,
        scopes=("preview:read",),
    )


def signed_grant() -> PreviewSignedGrant:
    header = {"alg": "EdDSA", "kid": "preview-key-2030-01", "typ": "FURA-PREVIEW-GRANT+jwt"}

    def encode(value: object) -> str:
        return base64.urlsafe_b64encode(canonical_preview_auth_json(value)).rstrip(b"=").decode()

    signature = base64.urlsafe_b64encode(b"s" * 64).rstrip(b"=").decode()
    compact = f"{encode(header)}.{encode(grant_claims().to_dict())}.{signature}"
    return PreviewSignedGrant(compact)


def jwks() -> PreviewJwks:
    coordinate = base64.urlsafe_b64encode(b"x" * 32).rstrip(b"=").decode()
    return PreviewJwks(
        issuer="https://auth.furatena.example/preview",
        keys=(
            PreviewJwk(
                "preview-key-2030-01", PreviewAuthAlgorithm.EDDSA, "OKP", "Ed25519", coordinate
            ),
        ),
        generated_at="2030-01-01T00:00:00Z",
        stale_after="2030-01-01T00:10:00Z",
    )


def revocation() -> PreviewRevocationSignal:
    return PreviewRevocationSignal(
        issuer="https://auth.furatena.example/preview",
        audience="furatena.preview",
        binding=binding(),
        subject="reviewer:stable-pseudonym",
        jti="grant_jti_12345678901234567890",
        key_id="preview-key-2030-01",
        revoked_at="2030-01-01T00:02:00Z",
        reason=PreviewAuthRevocationReason.HEAD_CHANGED,
        sequence=1,
    )


def structured_error() -> PreviewAuthError:
    return PreviewAuthError(
        error=PreviewAuthErrorCode.INVALID_BINDING,
        safe_message="The authorization request does not match this preview.",
        remediation="Restart authorization from the preview URL.",
        retryable=False,
        request_id=OPAQUE_A,
        redirect_allowed=False,
    )


SAMPLES = {
    "registration": registration,
    "authorization-request": authorization_request,
    "one-time-code": one_time_code,
    "grant-exchange": grant_exchange,
    "signed-grant": signed_grant,
    "jwks": jwks,
    "revocation": revocation,
    "error": structured_error,
}
