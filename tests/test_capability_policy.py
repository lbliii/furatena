"""Scoped capability policy decisions fail closed and remain deterministic."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any, Literal, cast

import pytest

from furatena.catalog.access import AccessPermission
from furatena.catalog.capability_policy import (
    CAPABILITY_EVALUATOR,
    Capability,
    CapabilityCacheMode,
    CapabilityCachePolicy,
    CapabilityDecision,
    CapabilityEffect,
    CapabilityPolicy,
    CapabilityRequest,
    CapabilityRule,
    CapabilityScope,
    CapabilitySubject,
    bind_trusted_capability_subject,
    capability_for_access_permission,
    capability_for_author_operation,
    capability_record_envelope,
    capability_record_from_envelope,
    require_current_publication_policy,
)
from furatena.catalog.gateway_identity import map_gateway_claims
from tests.publication_support import sample_plan


def _subject(
    *,
    roles: tuple[str, ...] = ("publisher",),
    teams: tuple[str, ...] = ("docs",),
    actor: str = "publisher@example.com",
) -> CapabilitySubject:
    return CapabilitySubject(
        actor=actor,
        identity_source="test-oidc",
        identity_fingerprint="trusted-fingerprint",
        roles=roles,
        teams=teams,
        tenant="acme",
        workspace="docs",
        site="developer",
        trusted=True,
    )


def _scope(**changes: object) -> CapabilityScope:
    values: dict[str, object] = {
        "tenant": "acme",
        "workspace": "docs",
        "site": "developer",
        "mount": "product",
        "path": "docs/guide.md",
        "lifecycle_state": "draft",
        "resulting_lifecycle": "public",
        "owners": ("publisher@example.com",),
        "owner_teams": ("docs",),
        "repository": "acme/product-docs",
        "branch": "main",
        "environment": "production",
        "sensitivity": "internal",
        "public_impact": True,
    }
    values.update(changes)
    return CapabilityScope(**cast(Any, values))


def _request(
    capability: Capability = Capability.PUBLISH,
    *,
    subject: CapabilitySubject | None = None,
    scope: CapabilityScope | None = None,
    plan_digest: str | None = None,
) -> CapabilityRequest:
    return CapabilityRequest(
        subject=subject or _subject(),
        capability=capability,
        scope=scope or _scope(),
        correlation_id="capability-test",
        evaluated_at="2030-01-01T00:00:00Z",
        plan_digest=plan_digest,
    )


def _allow_rule(
    *capabilities: Capability,
    rule_id: str = "publisher-production",
    **changes: object,
) -> CapabilityRule:
    values: dict[str, object] = {
        "rule_id": rule_id,
        "effect": CapabilityEffect.ALLOW,
        "priority": 10,
        "capabilities": capabilities or (Capability.PUBLISH,),
        "roles": ("publisher",),
        "tenants": ("acme",),
        "sites": ("developer",),
        "mounts": ("product",),
        "paths": ("docs/**",),
        "environments": ("production",),
        "required_approvals": 1,
        "eligible_roles": ("publisher",),
        "eligible_teams": ("docs",),
        "allow_self_approval": False,
        "separation_rules": ("author_cannot_approve",),
        "reason": "Publisher scope matched.",
        "remediation": "Proceed after required review.",
    }
    values.update(changes)
    return CapabilityRule(**cast(Any, values))


def _policy(*rules: CapabilityRule) -> CapabilityPolicy:
    return CapabilityPolicy.create(
        policy_version="capability-v1",
        rules=rules or (_allow_rule(Capability.PUBLISH),),
    )


def test_policy_digest_and_rule_selection_are_deterministic() -> None:
    broad = _allow_rule(
        Capability.PUBLISH,
        rule_id="broad",
        priority=10,
        paths=(),
        environments=(),
    )
    specific = _allow_rule(Capability.PUBLISH, rule_id="specific", priority=10)
    first = CapabilityPolicy.create(
        policy_version="capability-v1",
        rules=(broad, specific),
        extensions={"transport": "first"},
    )
    reordered = CapabilityPolicy.create(
        policy_version="capability-v1",
        rules=(specific, broad),
        extensions={"transport": "second"},
    )

    assert first.policy_digest == reordered.policy_digest
    assert CapabilityPolicy.from_dict(first.to_dict()) == first
    decision = CAPABILITY_EVALUATOR.evaluate(first, _request())
    assert decision.allowed is True
    assert decision.matched_rule_id == "specific"
    assert decision.approval_requirements.policy_digest == first.policy_digest
    assert decision.approval_requirements.required_count == 1
    assert decision.cacheability.mode == CapabilityCacheMode.NO_STORE
    assert CapabilityDecision.from_dict(decision.to_dict()) == decision

    normalized = _allow_rule(Capability.PUBLISH, paths=(r"docs\**",))
    assert normalized.paths == ("docs/**",)


def test_explicit_deny_overrides_allow_and_default_is_deny() -> None:
    allow = _allow_rule(Capability.PUBLISH)
    deny = CapabilityRule(
        rule_id="freeze-production",
        effect=CapabilityEffect.DENY,
        priority=1,
        capabilities=(Capability.PUBLISH,),
        environments=("production",),
        reason="Production publishing is frozen.",
        remediation="Wait for the freeze to end.",
    )
    denied = CAPABILITY_EVALUATOR.evaluate(_policy(allow, deny), _request())

    assert denied.allowed is False
    assert denied.reason_code == "rule_denied"
    assert denied.matched_rule_id == "freeze-production"
    assert denied.approval_requirements.required_count == 0

    unmatched = CAPABILITY_EVALUATOR.evaluate(_policy(allow), _request(Capability.DEPLOY))
    assert unmatched.allowed is False
    assert unmatched.reason_code == "default_deny"


def test_identity_scope_missing_context_and_untrusted_payloads_fail_closed() -> None:
    policy = _policy(_allow_rule(Capability.PUBLISH))
    cross_tenant = CAPABILITY_EVALUATOR.evaluate(
        policy,
        _request(scope=_scope(tenant="other")),
    )
    incomplete = CAPABILITY_EVALUATOR.evaluate(
        policy,
        _request(scope=_scope(path=None)),
    )
    trusted = _request()
    decoded = CapabilityRequest.from_dict(trusted.to_dict())
    spoofed = CAPABILITY_EVALUATOR.evaluate(policy, decoded)

    assert cross_tenant.reason_code == "identity_scope_mismatch"
    assert incomplete.reason_code == "context_incomplete"
    assert "path" in incomplete.reason
    assert spoofed.reason_code == "untrusted_identity"


def test_owner_team_lifecycle_repository_and_environment_scopes() -> None:
    rule = _allow_rule(
        Capability.PUBLISH,
        roles=(),
        teams=("docs",),
        require_owner=True,
        require_owner_team=True,
        repositories=("acme/*",),
        branches=("main",),
        lifecycle_states=("draft",),
        resulting_lifecycles=("public",),
        sensitivities=("internal",),
        public_impact=True,
    )
    policy = _policy(rule)

    assert CAPABILITY_EVALUATOR.evaluate(policy, _request()).allowed is True
    assert (
        CAPABILITY_EVALUATOR.evaluate(
            policy,
            _request(subject=_subject(actor="other@example.com")),
        ).reason_code
        == "default_deny"
    )
    assert (
        CAPABILITY_EVALUATOR.evaluate(
            policy,
            _request(scope=_scope(environment="staging")),
        ).reason_code
        == "default_deny"
    )


def test_all_required_duties_are_independently_expressible() -> None:
    subject = _subject(roles=("reviewer",), teams=("governance",))

    for capability in Capability:
        rule = _allow_rule(
            capability,
            rule_id=f"duty-{capability.value}",
            roles=("reviewer",),
            teams=("governance",),
            paths=(),
            mounts=(),
            environments=(),
            required_approvals=0,
            eligible_roles=(),
            eligible_teams=(),
            separation_rules=(),
        )
        decision = CAPABILITY_EVALUATOR.evaluate(
            _policy(rule),
            _request(capability, subject=subject),
        )
        assert decision.allowed is True, capability


def test_gateway_identity_is_trusted_but_raw_transport_identity_is_not() -> None:
    identity = map_gateway_claims(
        {
            "sub": "publisher@example.com",
            "roles": ["publisher"],
            "teams": ["docs"],
            "tenant": "acme",
            "workspace": "docs",
            "site": "developer",
        },
        trusted_transport=True,
    )
    subject = CapabilitySubject.from_gateway_identity(identity, identity_source="test-oidc")
    policy = _policy(_allow_rule(Capability.PUBLISH))

    request = _request(subject=subject)
    decoded = CapabilityRequest.from_dict(request.to_dict())

    assert CAPABILITY_EVALUATOR.evaluate(policy, request).allowed is True
    assert decoded.subject.trusted is False
    rebound = bind_trusted_capability_subject(decoded, subject)
    assert CAPABILITY_EVALUATOR.evaluate(policy, rebound).allowed is True
    with pytest.raises(PermissionError, match="not trusted"):
        bind_trusted_capability_subject(decoded, decoded.subject)


@pytest.mark.parametrize("transport", ["cli", "http", "mcp", "automation"])
def test_transport_envelopes_preserve_inner_contract(
    transport: Literal["cli", "http", "mcp", "automation"],
) -> None:
    request = _request()
    decision = CAPABILITY_EVALUATOR.evaluate(_policy(), _request())

    for record in (request, decision):
        envelope = capability_record_envelope(record, transport=transport)
        loaded = capability_record_from_envelope(envelope, transport=transport)
        assert loaded.to_dict() == record.to_dict()
        if isinstance(loaded, CapabilityRequest):
            assert loaded.subject.trusted is False


def test_publication_execution_rejects_policy_or_plan_drift() -> None:
    policy = _policy(_allow_rule(Capability.PUBLISH))
    plan = sample_plan(
        policy_version=policy.policy_version,
        policy_digest_value=policy.policy_digest,
    )
    decision = CAPABILITY_EVALUATOR.evaluate(
        policy,
        _request(Capability.PUBLISH, plan_digest=plan.plan_digest),
    )

    require_current_publication_policy(plan, decision, capability=Capability.PUBLISH)
    with pytest.raises(PermissionError, match="plan digest"):
        require_current_publication_policy(
            plan,
            replace(decision, plan_digest="sha256:" + "0" * 64),
            capability=Capability.PUBLISH,
        )
    with pytest.raises(PermissionError, match="policy is stale"):
        require_current_publication_policy(
            plan,
            replace(decision, policy_digest="sha256:" + "1" * 64),
            capability=Capability.PUBLISH,
        )


def test_legacy_permission_and_author_operation_adapters_are_stable() -> None:
    assert capability_for_access_permission(AccessPermission.READ) == Capability.READ
    assert capability_for_access_permission(AccessPermission.AUTHOR) == Capability.EDIT
    assert capability_for_access_permission(AccessPermission.PUBLISH) == Capability.PUBLISH
    assert capability_for_author_operation("validate") == Capability.VALIDATE
    assert capability_for_author_operation("unpublish") == Capability.UNPUBLISH
    assert capability_for_author_operation("archive") == Capability.ARCHIVE
    with pytest.raises(ValueError, match="unknown author"):
        capability_for_author_operation("impersonate")


def test_only_read_like_decisions_can_be_cacheable() -> None:
    cache = CapabilityCachePolicy(CapabilityCacheMode.PRIVATE, max_age_seconds=30)
    read_policy = _policy(
        _allow_rule(
            Capability.READ,
            paths=(),
            environments=(),
            required_approvals=0,
            eligible_roles=(),
            eligible_teams=(),
            separation_rules=(),
            cache=cache,
        )
    )
    publish_policy = _policy(_allow_rule(Capability.PUBLISH, cache=cache))

    assert (
        CAPABILITY_EVALUATOR.evaluate(read_policy, _request(Capability.READ)).cacheability == cache
    )
    assert (
        CAPABILITY_EVALUATOR.evaluate(
            publish_policy,
            _request(Capability.PUBLISH),
        ).cacheability.mode
        == CapabilityCacheMode.NO_STORE
    )


def test_legacy_role_lifecycle_matrix_is_preserved_by_explicit_grants() -> None:
    policy = _policy(
        _allow_rule(
            Capability.EDIT,
            rule_id="legacy-edit",
            roles=("contributor", "publisher", "admin"),
            paths=(),
            environments=(),
            required_approvals=0,
            eligible_roles=(),
            eligible_teams=(),
            separation_rules=(),
        ),
        _allow_rule(
            Capability.PUBLISH,
            Capability.UNPUBLISH,
            rule_id="legacy-publish",
            roles=("publisher", "admin"),
            paths=(),
            environments=(),
            required_approvals=0,
            eligible_roles=(),
            eligible_teams=(),
            separation_rules=(),
        ),
        _allow_rule(
            Capability.ARCHIVE,
            rule_id="legacy-archive",
            roles=("admin",),
            paths=(),
            environments=(),
            required_approvals=0,
            eligible_roles=(),
            eligible_teams=(),
            separation_rules=(),
        ),
    )
    expected = {
        "anonymous": set(),
        "reader": set(),
        "contributor": {"draft", "edit", "create"},
        "publisher": {"draft", "publish", "unpublish", "edit", "create"},
        "admin": {"draft", "publish", "unpublish", "archive", "edit", "create"},
    }
    operations = ("draft", "publish", "unpublish", "archive", "edit", "create")

    for role, allowed in expected.items():
        subject = _subject(roles=(role,))
        for operation in operations:
            capability = (
                Capability.EDIT
                if operation in {"edit", "create"}
                else capability_for_author_operation(operation)
            )
            decision = CAPABILITY_EVALUATOR.evaluate(
                policy,
                _request(capability, subject=subject),
            )
            assert decision.allowed is (operation in allowed), (role, operation)


def test_immutable_policy_evaluation_is_concurrency_safe() -> None:
    policy = _policy()
    request = _request()
    expected = CAPABILITY_EVALUATOR.evaluate(policy, request)

    with ThreadPoolExecutor(max_workers=8) as executor:
        decisions = list(
            executor.map(
                lambda _: CAPABILITY_EVALUATOR.evaluate(policy, request),
                range(200),
            )
        )

    assert all(decision == expected for decision in decisions)
