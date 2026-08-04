"""Shared golden outcomes for adversarial publication journeys across transports."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum


class PublicationConformanceTransport(StrEnum):
    BROWSER = "browser"
    CLI = "cli"
    MCP = "mcp"
    AUTOMATION = "automation"


class PublicationConformanceScenario(StrEnum):
    HAPPY_PATH = "create_validate_plan_approve_execute"
    STALE_SOURCE = "stale_source"
    STALE_PLAN = "stale_plan"
    STALE_VALIDATION = "stale_validation"
    STALE_POLICY = "stale_policy"
    STALE_CONFIGURATION = "stale_configuration"
    STALE_APPROVAL = "stale_approval"
    UNAUTHORIZED = "unauthorized"
    CROSS_TENANT = "cross_tenant"
    SELF_APPROVAL = "self_approval"
    EXPIRED_WAIVER = "expired_waiver"
    OVERRIDE = "override"
    DUPLICATE_SUBMIT = "duplicate_submit"
    RETRY = "retry"
    CONCURRENT_EXECUTION = "concurrent_execution"
    CRASH = "crash"
    RECONCILIATION = "reconciliation"
    GIT_CONFLICT = "git_conflict"
    GIT_PARTIAL_FAILURE = "git_partial_failure"
    BUILD_FAILURE = "build_failure"
    PRIVACY_LEAK = "privacy_leak"
    FAILED_PROMOTION = "failed_promotion"
    VERIFICATION_FAILURE = "verification_failure"
    ROLLBACK = "rollback"


class PublicationConformanceStatus(StrEnum):
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    STALE = "stale"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True)
class PublicationConformanceExpectation:
    status: PublicationConformanceStatus
    policy_allowed: bool
    partial_success_allowed: bool = False


@dataclass(frozen=True, slots=True)
class PublicationConformanceOutcome:
    scenario: PublicationConformanceScenario
    transport: PublicationConformanceTransport
    status: PublicationConformanceStatus
    policy_allowed: bool
    partial_success: bool
    code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario", PublicationConformanceScenario(self.scenario))
        object.__setattr__(self, "transport", PublicationConformanceTransport(self.transport))
        object.__setattr__(self, "status", PublicationConformanceStatus(self.status))
        if not self.code.strip():
            raise ValueError("Publication conformance outcome code must contain a non-empty value.")

    def golden_tuple(self) -> tuple[object, ...]:
        return (self.status, self.policy_allowed, self.partial_success, self.code)


_STALE = PublicationConformanceExpectation(PublicationConformanceStatus.STALE, True)
_DENIED = PublicationConformanceExpectation(PublicationConformanceStatus.DENIED, False)
_FAILED = PublicationConformanceExpectation(PublicationConformanceStatus.FAILED, True)
_SUCCEEDED = PublicationConformanceExpectation(PublicationConformanceStatus.SUCCEEDED, True)

PUBLICATION_CONFORMANCE_EXPECTATIONS: Mapping[
    PublicationConformanceScenario, PublicationConformanceExpectation
] = {
    PublicationConformanceScenario.HAPPY_PATH: _SUCCEEDED,
    PublicationConformanceScenario.STALE_SOURCE: _STALE,
    PublicationConformanceScenario.STALE_PLAN: _STALE,
    PublicationConformanceScenario.STALE_VALIDATION: _STALE,
    PublicationConformanceScenario.STALE_POLICY: _STALE,
    PublicationConformanceScenario.STALE_CONFIGURATION: _STALE,
    PublicationConformanceScenario.STALE_APPROVAL: _STALE,
    PublicationConformanceScenario.UNAUTHORIZED: _DENIED,
    PublicationConformanceScenario.CROSS_TENANT: _DENIED,
    PublicationConformanceScenario.SELF_APPROVAL: _DENIED,
    PublicationConformanceScenario.EXPIRED_WAIVER: _DENIED,
    PublicationConformanceScenario.OVERRIDE: _DENIED,
    PublicationConformanceScenario.DUPLICATE_SUBMIT: _SUCCEEDED,
    PublicationConformanceScenario.RETRY: _SUCCEEDED,
    PublicationConformanceScenario.CONCURRENT_EXECUTION: _SUCCEEDED,
    PublicationConformanceScenario.CRASH: PublicationConformanceExpectation(
        PublicationConformanceStatus.RECONCILIATION_REQUIRED,
        True,
    ),
    PublicationConformanceScenario.RECONCILIATION: _SUCCEEDED,
    PublicationConformanceScenario.GIT_CONFLICT: _FAILED,
    PublicationConformanceScenario.GIT_PARTIAL_FAILURE: PublicationConformanceExpectation(
        PublicationConformanceStatus.RECONCILIATION_REQUIRED,
        True,
    ),
    PublicationConformanceScenario.BUILD_FAILURE: _FAILED,
    PublicationConformanceScenario.PRIVACY_LEAK: _FAILED,
    PublicationConformanceScenario.FAILED_PROMOTION: _FAILED,
    PublicationConformanceScenario.VERIFICATION_FAILURE: _FAILED,
    PublicationConformanceScenario.ROLLBACK: PublicationConformanceExpectation(
        PublicationConformanceStatus.ROLLED_BACK,
        True,
    ),
}


def validate_publication_conformance(
    outcomes: Iterable[PublicationConformanceOutcome],
) -> tuple[str, ...]:
    """Return deterministic failures for one complete cross-transport result matrix."""

    indexed: dict[
        tuple[PublicationConformanceScenario, PublicationConformanceTransport],
        PublicationConformanceOutcome,
    ] = {}
    failures: list[str] = []
    for outcome in outcomes:
        key = (outcome.scenario, outcome.transport)
        if key in indexed:
            failures.append(
                f"duplicate outcome for {outcome.scenario.value}/{outcome.transport.value}"
            )
        indexed[key] = outcome
    for scenario, expectation in PUBLICATION_CONFORMANCE_EXPECTATIONS.items():
        golden: tuple[object, ...] | None = None
        for transport in PublicationConformanceTransport:
            outcome = indexed.get((scenario, transport))
            if outcome is None:
                failures.append(f"missing outcome for {scenario.value}/{transport.value}")
                continue
            if outcome.status != expectation.status:
                failures.append(
                    f"{scenario.value}/{transport.value} returned {outcome.status.value}; "
                    f"expected {expectation.status.value}"
                )
            if outcome.policy_allowed != expectation.policy_allowed:
                failures.append(
                    f"{scenario.value}/{transport.value} policy decision diverged from golden"
                )
            if outcome.partial_success and not expectation.partial_success_allowed:
                failures.append(
                    f"{scenario.value}/{transport.value} falsely reported partial success"
                )
            if golden is None:
                golden = outcome.golden_tuple()
            elif outcome.golden_tuple() != golden:
                failures.append(
                    f"{scenario.value}/{transport.value} diverged from the shared transport outcome"
                )
    extras = set(indexed) - {
        (scenario, transport)
        for scenario in PUBLICATION_CONFORMANCE_EXPECTATIONS
        for transport in PublicationConformanceTransport
    }
    failures.extend(
        f"unexpected outcome for {scenario.value}/{transport.value}"
        for scenario, transport in sorted(extras, key=lambda item: (item[0].value, item[1].value))
    )
    return tuple(failures)
