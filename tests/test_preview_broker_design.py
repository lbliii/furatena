"""Drift guards for the hosted preview broker design boundary."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARCHITECTURE = REPO / "docs" / "PREVIEW_BROKER_ARCHITECTURE.md"
THREAT_MODEL = REPO / "docs" / "PREVIEW_BROKER_THREAT_MODEL.md"


def _section(document: str, heading: str) -> str:
    marker = f"## {heading}\n"
    assert marker in document
    section = document.split(marker, 1)[1]
    return section.split("\n## ", 1)[0]


def _table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if all(cell and set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    return rows


def test_broker_architecture_keeps_service_protocol_and_hot_path_separate() -> None:
    document = ARCHITECTURE.read_text(encoding="utf-8")

    assert "Status: Accepted for v1" in document
    assert "dedicated `furatena-preview-broker` repository" in document
    assert "main Furatena repository and distribution" in document
    assert "There is intentionally no browser/client → preview → broker proxy path" in document
    assert "never consulted" in document
    assert "ordinary page request" in document
    assert (
        "Trusted controller -- OIDC + exact binding --> Broker control edge --> Store" in document
    )
    assert "Preview runtime <---- JWKS + scoped revocation ---- Broker protocol edge" in document
    assert "This decision is a design and review boundary only" in document

    for heading in (
        "Ownership and release boundaries",
        "Trust boundaries and principals",
        "Data flows",
        "Endpoint inventory",
        "Least-privilege GitHub App policy",
        "Tenant and storage boundary",
        "Signing-key and secret custody",
        "Failure and degraded-operation contract",
        "Service objectives and support",
        "Cost and capacity envelope",
        "General-availability gates",
        "Public and operator follow-ups",
        "Explicit exclusions",
    ):
        assert f"## {heading}\n" in document


def test_broker_endpoint_inventory_is_stable_bounded_and_complete() -> None:
    section = _section(ARCHITECTURE.read_text(encoding="utf-8"), "Endpoint inventory")
    rows = _table_rows(section)
    endpoints = {
        match.group(1)
        for row in rows[1:]
        if (match := re.fullmatch(r"`([^`]+)`", row[0])) is not None
    }

    assert endpoints == {
        "POST /preview/authorize",
        "GET /preview/device",
        "POST /preview/device",
        "GET /preview/github/callback",
        "POST /preview/grants",
        "GET /preview/jwks.json",
        "POST /preview/revocations",
        "PUT /control/v1/registrations",
        "POST /control/v1/registrations/close",
        "GET /healthz",
        "GET /readyz",
    }
    assert len(endpoints) == len(rows) - 1
    assert all(len(row) == 4 and all(row) for row in rows)
    assert "Secrets are prohibited in\npaths and ordinary query strings" in section


def test_oidc_workflow_identity_is_not_confused_with_registration_intent() -> None:
    architecture = ARCHITECTURE.read_text(encoding="utf-8")
    threat_model = THREAT_MODEL.read_text(encoding="utf-8")

    assert (
        "OIDC claims authenticate the trusted workflow context, not the submitted\n"
        "pull-request number, head SHA, or preview origin."
    ) in architecture
    assert (
        "The binding fields therefore remain request intent until independently\n"
        "revalidated against current GitHub state and the deployment-provider/controller\n"
        "authority selected by #521."
    ) in architecture
    assert (
        "GitHub OIDC establishes the trusted workflow principal; it does not assert the\n"
        "submitted pull-request number, pull-request head SHA, or preview origin."
    ) in threat_model
    assert "control payload and handoff topology remain\nowned by that implementation" in (
        threat_model
    )
    assert "event, audience, PR, SHA, and time" not in architecture
    assert "event/ref/PR/SHA/time" not in threat_model


def test_github_app_permission_matrix_cannot_silently_expand() -> None:
    section = _section(
        ARCHITECTURE.read_text(encoding="utf-8"),
        "Least-privilege GitHub App policy",
    )
    rows = _table_rows(section)
    by_permission = {row[0]: row[1:] for row in rows[1:]}

    assert by_permission["Repository metadata"][0] == "Read, required"
    assert by_permission["Pull requests"][0] == "Read, required"
    assert by_permission["Organization members"][0] == "None by default; optional read only"
    denied = next(key for key in by_permission if key.startswith("Contents, actions"))
    assert by_permission[denied][0] == "None"
    assert "cannot turn on organization-member read globally" in section
    assert "fails that restricted authorization closed" in section


def test_retained_field_contract_has_complete_rules_and_no_secret_retention() -> None:
    document = THREAT_MODEL.read_text(encoding="utf-8")
    section = _section(document, "Retained-field contract")
    rows = _table_rows(section)

    assert len(rows) >= 23
    assert all(len(row) == 6 and all(row) for row in rows)
    assert rows[0] == [
        "Retained field or field group",
        "Purpose",
        "Maximum retention",
        "Deletion rule",
        "Access control",
        "Redaction/export rule",
    ]
    assert "Signed grant" in {row[0] for row in rows}
    assert "GitHub user token, provider OAuth code, and user profile response" in {
        row[0] for row in rows
    }
    signed_grant = next(row for row in rows if row[0] == "Signed grant")
    github_token = next(row for row in rows if row[0].startswith("GitHub user token"))
    assert signed_grant[2] == "Not retained"
    assert github_token[2].startswith("Transaction memory only")
    assert "No other field may be retained without updating this table" in section


def test_threat_model_covers_required_failures_and_release_evidence() -> None:
    document = THREAT_MODEL.read_text(encoding="utf-8")
    scenarios = _section(document, "Abuse cases and mitigations")

    for required in (
        "Registration replay",
        "Origin takeover, DNS reuse, or stale domain",
        "Removed user access",
        "Deleted installation or repository access removal",
        "GitHub outage or rate limit",
        "Broker outage",
        "Signing-key compromise",
        "Region, DNS, certificate, or issuer-domain failure",
    ):
        assert required in scenarios

    assert "## Verification obligations\n" in document
    assert "## Separate disclosure and runbook deliverables\n" in document
    assert "does not claim that the service exists" in document
    assert not re.search(r"\b(?:TODO|TBD|FIXME)\b", document)
