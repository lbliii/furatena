"""Scenario fixtures exercise complete versioned publication workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from furatena.catalog.publication_contracts import (
    PublicationDecision,
    PublicationDecisionKind,
    PublicationEvent,
    PublicationOperation,
    PublicationOutputIntent,
    PublicationOutputReference,
    PublicationRecordReference,
    PublicationState,
    PublicationStateSnapshot,
)
from furatena.catalog.publication_state import (
    PublicationTransitionGuards,
    transition_publication_state,
)
from tests.publication_support import sample_actor, sample_plan

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "publication" / "v1"
EXPECTED_SCENARIOS = {
    "deploy",
    "expiry",
    "protected-pr",
    "rejection",
    "rollback",
    "solo-local",
    "supersession",
}


def _load_fixtures() -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(FIXTURE_ROOT.glob("*.json"))
    ]


def test_publication_scenario_fixture_inventory_is_exact() -> None:
    fixtures = _load_fixtures()

    assert {fixture["scenario"] for fixture in fixtures} == EXPECTED_SCENARIOS
    assert all(fixture["fixture_version"] == 1 for fixture in fixtures)


def test_publication_scenario_fixtures_reach_expected_immutable_state() -> None:
    for fixture in _load_fixtures():
        output = fixture["output"]
        plan = sample_plan(
            correlation_id=f"fixture-{fixture['scenario']}",
            idempotency_key=f"fixture-{fixture['scenario']}",
            operation=PublicationOperation(fixture["operation"]),
            required_count=fixture["required_approvals"],
            expires_at=fixture["expires_at"],
            outputs=(
                PublicationOutputIntent(
                    kind=output["kind"],
                    target=output["target"],
                    environment=output["environment"],
                ),
            ),
        )
        decisions = tuple(
            PublicationDecision.create(
                plan_digest=plan.plan_digest,
                policy_digest=plan.bindings.policy_digest,
                actor=sample_actor(item["actor"]),
                decision=PublicationDecisionKind(item["decision"]),
                reason=item["reason"],
                timestamp=f"2030-01-01T00:{index + 1:02}:30Z",
            )
            for index, item in enumerate(fixture["decisions"])
        )
        decision_refs = tuple(
            PublicationRecordReference(
                record_id=decision.decision_id,
                record_digest=decision.decision_digest,
            )
            for decision in decisions
        )
        approvals_satisfied = any(
            decision.decision == PublicationDecisionKind.APPROVE for decision in decisions
        )
        snapshot = PublicationStateSnapshot.initial(
            plan,
            timestamp="2030-01-01T00:00:00Z",
        )
        events: list[PublicationEvent] = []

        for index, state_name in enumerate(fixture["transitions"], start=1):
            target = PublicationState(state_name)
            timestamp = (
                "2030-01-01T00:03:00Z"
                if target == PublicationState.EXPIRED
                else f"2030-01-01T00:{index:02}:00Z"
            )
            applied_outputs = (
                (
                    PublicationOutputReference(
                        kind=output["kind"],
                        identifier=f"fixture-{fixture['scenario']}",
                        status="applied",
                        revision="fixture-revision",
                        url="https://provider.invalid/private/output",
                    ),
                )
                if target == PublicationState.APPLIED
                else None
            )
            transition = transition_publication_state(
                plan,
                snapshot,
                target,
                expected_state_version=snapshot.state_version,
                actor=sample_actor(),
                timestamp=timestamp,
                guards=PublicationTransitionGuards(
                    approvals_satisfied=approvals_satisfied,
                ),
                decision_refs=decision_refs,
                outputs=applied_outputs,
            )
            snapshot = transition.snapshot
            events.append(transition.event)

        assert snapshot.state == PublicationState(fixture["expected_state"])
        assert snapshot.state_version == len(events) + 1
        assert snapshot.terminal is True
        assert all(
            PublicationDecision.from_dict(decision.to_dict()) == decision for decision in decisions
        )
        assert all(PublicationEvent.from_dict(event.to_dict()) == event for event in events)
        assert PublicationStateSnapshot.from_dict(snapshot.to_dict()) == snapshot
