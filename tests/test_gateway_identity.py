"""Trusted gateway and provider-neutral SSO mapping contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from furatena.catalog.access import AccessRole, AccessSubject
from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.config import CatalogIdentityConfig
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.gateway_identity import (
    GatewayClaimMapping,
    GatewayIdentityError,
    TrustedGatewayPolicy,
    identity_from_trusted_session,
    map_gateway_claims,
)


def test_oidc_style_claims_map_to_subject_and_catalog_identity() -> None:
    identity = map_gateway_claims(
        {
            "sub": "user-123",
            "roles": ["reader", "publisher"],
            "teams": "docs platform",
            "tid": "acme",
            "workspace_id": "developer-experience",
            "site_id": "developer-docs",
        },
        trusted_transport=True,
        expected_identity={
            "tenant": "acme",
            "workspace": "developer-experience",
            "site": "developer-docs",
        },
    )

    assert identity.subject.actor == "user-123"
    assert identity.subject.roles == frozenset(
        {AccessRole.READER, AccessRole.PUBLISHER}
    )
    assert identity.subject.teams == frozenset({"docs", "platform"})
    assert (identity.tenant, identity.workspace, identity.site) == (
        "acme",
        "developer-experience",
        "developer-docs",
    )
    assert len(identity.claim_fingerprint) == 64


def test_custom_provider_claim_names_are_supported_without_provider_code() -> None:
    policy = TrustedGatewayPolicy(
        mapping=GatewayClaimMapping(
            actor=("user_name",),
            roles=("entitlements",),
            teams=("groups",),
            tenant=("organization",),
            workspace=("space",),
            site=("application",),
        ),
        allowed_roles=frozenset({AccessRole.READER, AccessRole.CONTRIBUTOR}),
        allowed_tenants=frozenset({"acme"}),
        allowed_sites=frozenset({"support"}),
    )
    identity = map_gateway_claims(
        {
            "user_name": "writer@example.com",
            "entitlements": "reader,contributor",
            "groups": ["support-docs"],
            "organization": "acme",
            "space": "knowledge",
            "application": "support",
        },
        trusted_transport=True,
        policy=policy,
    )

    assert identity.subject.has_role_at_least(AccessRole.CONTRIBUTOR)
    assert identity.subject.teams == frozenset({"support-docs"})


def test_transport_trust_is_not_accepted_from_spoofable_claims() -> None:
    with pytest.raises(GatewayIdentityError) as caught:
        map_gateway_claims(
            {
                "verified": True,
                "sub": "attacker",
                "roles": ["admin"],
                "tenant": "acme",
                "site": "developer-docs",
            },
            trusted_transport=False,
        )

    assert caught.value.code == "untrusted_transport"
    assert caught.value.to_diagnostic()["rule_id"] == (
        "fura.identity.untrusted_transport"
    )


@pytest.mark.parametrize(
    ("claims", "code"),
    [
        (
            {"sub": "user", "tenant": "acme", "site": "docs"},
            "missing_claim",
        ),
        (
            {
                "sub": "user-a",
                "actor": "user-b",
                "roles": ["reader"],
                "tenant": "acme",
                "site": "docs",
            },
            "conflicting_claim",
        ),
        (
            {
                "sub": "user",
                "roles": ["super-admin"],
                "tenant": "acme",
                "site": "docs",
            },
            "unknown_role",
        ),
        (
            {
                "sub": "user",
                "roles": ["reader"],
                "tenant": "other",
                "workspace": "platform",
                "site": "docs",
            },
            "identity_conflict",
        ),
    ],
)
def test_missing_conflicting_unknown_and_cross_tenant_claims_fail_closed(
    claims: dict[str, object],
    code: str,
) -> None:
    expected = (
        {"tenant": "acme", "workspace": "platform", "site": "docs"}
        if code == "identity_conflict"
        else None
    )
    with pytest.raises(GatewayIdentityError) as caught:
        map_gateway_claims(
            claims,
            trusted_transport=True,
            expected_identity=expected,
        )
    assert caught.value.code == code


def test_server_owned_session_round_trip_rejects_tampering() -> None:
    identity = map_gateway_claims(
        {
            "sub": "author",
            "roles": ["contributor"],
            "teams": ["docs"],
            "tenant": "acme",
            "workspace": "platform",
            "site": "docs",
        },
        trusted_transport=True,
    )
    session = identity.to_session()

    restored = identity_from_trusted_session(
        session,
        expected_identity={
            "tenant": "acme",
            "workspace": "platform",
            "site": "docs",
        },
    )
    assert restored == identity

    tampered = {**session, "roles": ["admin"]}
    with pytest.raises(GatewayIdentityError, match="fingerprint") as caught:
        identity_from_trusted_session(tampered)
    assert caught.value.code == "session_conflict"

    with pytest.raises(GatewayIdentityError) as untrusted:
        identity_from_trusted_session({**session, "source": "request_headers"})
    assert untrusted.value.code == "untrusted_session"


def test_browser_author_subject_fails_closed_on_invalid_gateway_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = map_gateway_claims(
        {
            "sub": "author",
            "roles": ["contributor"],
            "tenant": "acme",
            "workspace": "platform",
            "site": "docs",
        },
        trusted_transport=True,
    )
    session = {"fura_author_subject": identity.to_session()}
    docs = SimpleNamespace(
        config=SimpleNamespace(
            identity=CatalogIdentityConfig(
                tenant="acme",
                workspace="platform",
                site="docs",
            )
        ),
        author_subject=AccessSubject.anonymous(),
    )
    monkeypatch.setattr("furatena.catalog.docs_app.get_session", lambda: session)

    subject = DocsApp._browser_author_subject(docs)
    assert subject.actor == "author"
    assert subject.has_role_at_least(AccessRole.CONTRIBUTOR)

    session["fura_author_subject"] = {
        **identity.to_session(),
        "tenant": "other",
    }
    assert DocsApp._browser_author_subject(docs) == AccessSubject.anonymous()


def test_claim_mapping_is_safe_under_free_threading() -> None:
    assert_free_threading()
    claims = {
        "sub": "reader",
        "roles": ["reader"],
        "teams": ["docs"],
        "tenant": "acme",
        "workspace": "platform",
        "site": "docs",
    }

    with ThreadPoolExecutor(max_workers=16) as pool:
        identities = list(
            pool.map(
                lambda _index: map_gateway_claims(
                    claims,
                    trusted_transport=True,
                    expected_identity={
                        "tenant": "acme",
                        "workspace": "platform",
                        "site": "docs",
                    },
                ),
                range(512),
            )
        )

    assert len({item.claim_fingerprint for item in identities}) == 1
    assert all(item.subject.actor == "reader" for item in identities)
