"""Deterministic beta adoption-readiness scorecards."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

from furatena.catalog.deployment_profiles import DEPLOYMENT_PROFILES

SCHEMA_VERSION = 1
POLICY_ID = "furatena-beta-adoption-readiness"
POLICY_VERSION = "1.0.0"
AREAS = (
    "activation",
    "migration",
    "build",
    "retrieval",
    "agent",
    "buyer_confidence",
)
_PROFILE_IDS = frozenset(profile.id for profile in DEPLOYMENT_PROFILES)


def load_adoption_evidence(path: Path) -> dict[str, Any]:
    """Load and validate one versioned scorecard evidence manifest."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("adoption evidence must be a JSON object")
    return raw


def build_adoption_scorecard(evidence_manifest: dict[str, Any]) -> dict[str, Any]:
    """Evaluate fixed adoption gates against explicit, reproducible evidence."""
    manifest = _validated_manifest(evidence_manifest)
    evidence = manifest["evidence"]
    gates = [
        _maximum_gate(
            "activation.new_site_first_edit",
            "activation",
            "Median clone-to-first-edit time for new sites",
            evidence["activation"]["new_site_first_edit_median_seconds"],
            600.0,
        ),
        _maximum_gate(
            "activation.imported_site_first_edit",
            "activation",
            "Median clone-to-first-edit time for imported sites",
            evidence["activation"]["imported_site_first_edit_median_seconds"],
            1800.0,
        ),
        _minimum_gate(
            "activation.first_publish_measured",
            "activation",
            "Opted-in sessions with a first-publish milestone",
            evidence["activation"]["first_publish_sample_count"],
            1.0,
        ),
        _minimum_gate(
            "activation.clean_migration_measured",
            "activation",
            "Imported-site sessions with a clean-migration milestone",
            evidence["activation"]["clean_migration_sample_count"],
            1.0,
        ),
        _minimum_gate(
            "migration.clean_page_ratio",
            "migration",
            "Share of indexed pages clean after the first automated pass",
            evidence["migration"]["clean_page_ratio"],
            0.9,
        ),
        _equals_gate(
            "migration.blockers_grouped",
            "migration",
            "Remaining blockers are grouped by owner and source",
            evidence["migration"]["blockers_grouped_by_owner_source"],
            True,
        ),
        _strict_maximum_gate(
            "build.check_p95",
            "build",
            "P95 check duration in seconds",
            evidence["build"]["check_p95_seconds"],
            300.0,
        ),
        _strict_maximum_gate(
            "build.static_export_p95",
            "build",
            "P95 static-export duration in seconds",
            evidence["build"]["static_export_p95_seconds"],
            600.0,
        ),
        _equals_gate(
            "build.pdf_batch_documented",
            "build",
            "Representative PDF batch duration and corpus are documented",
            evidence["build"]["pdf_batch_documented"],
            True,
        ),
        _minimum_gate(
            "retrieval.recall_at_3",
            "retrieval",
            "Known-answer top-3 retrieval rate before tuning",
            evidence["retrieval"]["recall_at_3"],
            0.8,
        ),
        _maximum_gate(
            "retrieval.private_leaks",
            "retrieval",
            "Private results leaked by public retrieval",
            evidence["retrieval"]["private_leaks"],
            0.0,
        ),
        _maximum_gate(
            "agent.error_count",
            "agent",
            "Agent evaluation errors",
            evidence["agent"]["error_count"],
            0.0,
        ),
        _maximum_gate(
            "agent.private_leaks",
            "agent",
            "Private results leaked by public agent tools",
            evidence["agent"]["private_leaks"],
            0.0,
        ),
        _equals_gate(
            "agent.warnings_actionable",
            "agent",
            "Every stale or safety warning has explicit remediation",
            evidence["agent"]["warnings_with_remediation"],
            True,
        ),
        _one_of_gate(
            "buyer_confidence.deployment_profile",
            "buyer_confidence",
            "A supported deployment profile is selected",
            evidence["buyer_confidence"]["deployment_profile"],
            sorted(_PROFILE_IDS),
        ),
        _equals_gate(
            "buyer_confidence.approval_blockers_named",
            "buyer_confidence",
            "Remaining approval blockers are named",
            evidence["buyer_confidence"]["approval_blockers_named"],
            True,
        ),
    ]
    unmet = []
    for gate in gates:
        if gate["passed"]:
            continue
        ownership = evidence[gate["area"]]
        unmet.append(
            {
                "gate_id": gate["id"],
                "owner": ownership["owner"],
                "remediation": ownership["remediation"],
                "observed": gate["observed"],
                "target": gate["target"],
            }
        )
    digest = hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()
    passed_count = sum(bool(gate["passed"]) for gate in gates)
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": {
            "id": POLICY_ID,
            "version": POLICY_VERSION,
            "definitions": "docs/concepts/adoption-research#success-metrics",
        },
        "decision_date": manifest["decision_date"],
        "decision": "go" if not unmet else "no-go",
        "ok": not unmet,
        "evidence_sha256": digest,
        "gate_count": len(gates),
        "passed_gate_count": passed_count,
        "unmet_gate_count": len(unmet),
        "gates": gates,
        "unmet_gates": unmet,
    }


def write_adoption_scorecard(report: dict[str, Any], path: Path | None) -> str:
    """Serialize a scorecard and optionally write the report."""
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    return payload


def _validated_manifest(raw: dict[str, Any]) -> dict[str, Any]:
    _exact_keys(raw, {"schema_version", "decision_date", "evidence"}, "manifest")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported adoption evidence schema: {raw['schema_version']!r}")
    decision_date = str(raw["decision_date"])
    try:
        date.fromisoformat(decision_date)
    except ValueError as exc:
        raise ValueError("decision_date must be an ISO YYYY-MM-DD date") from exc
    evidence = raw["evidence"]
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    _exact_keys(evidence, set(AREAS), "evidence")
    expected = {
        "activation": {
            "owner", "remediation", "new_site_first_edit_median_seconds",
            "imported_site_first_edit_median_seconds", "first_publish_sample_count",
            "clean_migration_sample_count",
        },
        "migration": {
            "owner", "remediation", "clean_page_ratio",
            "blockers_grouped_by_owner_source",
        },
        "build": {
            "owner", "remediation", "check_p95_seconds",
            "static_export_p95_seconds", "pdf_batch_documented",
        },
        "retrieval": {"owner", "remediation", "recall_at_3", "private_leaks"},
        "agent": {
            "owner", "remediation", "error_count", "private_leaks",
            "warnings_with_remediation",
        },
        "buyer_confidence": {
            "owner", "remediation", "deployment_profile", "approval_blockers_named",
        },
    }
    numeric = {
        "activation": (
            "new_site_first_edit_median_seconds",
            "imported_site_first_edit_median_seconds",
            "first_publish_sample_count",
            "clean_migration_sample_count",
        ),
        "migration": ("clean_page_ratio",),
        "build": ("check_p95_seconds", "static_export_p95_seconds"),
        "retrieval": ("recall_at_3", "private_leaks"),
        "agent": ("error_count", "private_leaks"),
    }
    boolean = {
        "migration": ("blockers_grouped_by_owner_source",),
        "build": ("pdf_batch_documented",),
        "agent": ("warnings_with_remediation",),
        "buyer_confidence": ("approval_blockers_named",),
    }
    for area in AREAS:
        section = evidence[area]
        if not isinstance(section, dict):
            raise ValueError(f"evidence.{area} must be an object")
        _exact_keys(section, expected[area], f"evidence.{area}")
        for field in ("owner", "remediation"):
            if not isinstance(section[field], str) or not section[field].strip():
                raise ValueError(f"evidence.{area}.{field} must be a non-empty string")
        for field in numeric.get(area, ()):
            _optional_number(section[field], f"evidence.{area}.{field}")
        for field in boolean.get(area, ()):
            if section[field] is not None and not isinstance(section[field], bool):
                raise ValueError(f"evidence.{area}.{field} must be boolean or null")
    migration_ratio = evidence["migration"]["clean_page_ratio"]
    retrieval_rate = evidence["retrieval"]["recall_at_3"]
    for value, label in (
        (migration_ratio, "evidence.migration.clean_page_ratio"),
        (retrieval_rate, "evidence.retrieval.recall_at_3"),
    ):
        if value is not None and not 0 <= float(value) <= 1:
            raise ValueError(f"{label} must be between 0 and 1")
    profile = evidence["buyer_confidence"]["deployment_profile"]
    if profile is not None and not isinstance(profile, str):
        raise ValueError("evidence.buyer_confidence.deployment_profile must be a string or null")
    return raw


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label} fields do not match schema; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _optional_number(value: Any, label: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be a finite non-negative number or null")
    if not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"{label} must be a finite non-negative number or null")


def _gate(
    gate_id: str,
    area: str,
    definition: str,
    observed: Any,
    operator: str,
    target: Any,
    passed: bool,
) -> dict[str, Any]:
    return {
        "id": gate_id,
        "area": area,
        "definition": definition,
        "observed": observed,
        "target": {"operator": operator, "value": target},
        "passed": passed,
    }


def _maximum_gate(
    gate_id: str, area: str, definition: str, observed: float | None, target: float
) -> dict[str, Any]:
    return _gate(
        gate_id, area, definition, observed, "<=", target,
        observed is not None and float(observed) <= target,
    )


def _strict_maximum_gate(
    gate_id: str, area: str, definition: str, observed: float | None, target: float
) -> dict[str, Any]:
    return _gate(
        gate_id, area, definition, observed, "<", target,
        observed is not None and float(observed) < target,
    )


def _minimum_gate(
    gate_id: str, area: str, definition: str, observed: float | None, target: float
) -> dict[str, Any]:
    return _gate(
        gate_id, area, definition, observed, ">=", target,
        observed is not None and float(observed) >= target,
    )


def _equals_gate(
    gate_id: str, area: str, definition: str, observed: bool | None, target: bool
) -> dict[str, Any]:
    return _gate(gate_id, area, definition, observed, "==", target, observed is target)


def _one_of_gate(
    gate_id: str, area: str, definition: str, observed: str | None, target: list[str]
) -> dict[str, Any]:
    return _gate(gate_id, area, definition, observed, "one_of", target, observed in target)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
