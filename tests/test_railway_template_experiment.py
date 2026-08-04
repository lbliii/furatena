"""Privacy and decision contracts for the Railway template experiment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
POLICY_PATH = ROOT / "config" / "railway-template-experiment.json"


def _walk_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _walk_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _walk_keys(item)}
    return set()


def test_experiment_policy_is_pre_observation_and_dependency_gated() -> None:
    policy = json.loads(POLICY_PATH.read_text())

    assert policy["schema_version"] == 1
    assert policy["tracking_issue"] == 460
    assert policy["status"] == "prelaunch_blocked_on_458"
    assert policy["review_days"] == [0, 30, 60, 90]
    assert policy["github_traffic_capture_interval_days"] <= 14


def test_experiment_policy_has_one_deterministic_day_90_outcome() -> None:
    policy = json.loads(POLICY_PATH.read_text())
    decisions = policy["decision_policy"]

    assert decisions["precedence"] == [
        "stop",
        "approve_one_variant",
        "continue",
        "change",
    ]
    assert decisions["approve_one_variant"]["maximum_variants"] == 1
    assert (
        policy["hypotheses"]["reliable_delivery"]["security_or_privacy_boundary_violations_allowed"]
        == 0
    )


def test_experiment_policy_forbids_identity_credentials_and_payout_data() -> None:
    policy = json.loads(POLICY_PATH.read_text())
    allowlist = set(policy["snapshot_field_allowlist"])
    forbidden = set(policy["forbidden_fields"])

    assert allowlist.isdisjoint(forbidden)
    assert {
        "user_id",
        "private_project_name",
        "support_question_text",
        "payout_amount",
        "railway_token",
        "customer_content",
    } <= forbidden
    assert not ({"snapshot", "observations", "results"} & _walk_keys(policy))
    assert policy["public_summary"] == {
        "allowed_earnings_results": ["positive", "zero", "not_observable"],
        "amounts_allowed": False,
        "payout_data_allowed": False,
        "private_cost_or_scale_allowed": False,
    }
