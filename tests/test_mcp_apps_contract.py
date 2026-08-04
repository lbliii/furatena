"""Versioned MCP Apps negotiation, security, and gateway contracts."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from furatena.catalog.agent_lint import check_mcp_app_contracts
from furatena.catalog.mcp import FuraMCPServer
from furatena.catalog.mcp_apps import (
    MCP_APP_MIME_TYPE,
    MCP_APPS_EXTENSION_ID,
    MCPAppContractError,
    client_supports_mcp_apps,
    rewrite_mcp_app_uri,
    validate_canonical_app_uri,
)

FIXTURES = Path(__file__).parent / "fixtures" / "agent-contracts" / "v1"


def _apps_fixture(profile: str) -> dict:
    payload = json.loads((FIXTURES / f"{profile}.json").read_text(encoding="utf-8"))
    return payload["surfaces"]["mcp_apps"]


def test_mcp_apps_capability_is_explicitly_negotiated() -> None:
    params = {
        "protocolVersion": "2025-06-18",
        "capabilities": {
            "extensions": {
                MCP_APPS_EXTENSION_ID: {"mimeTypes": [MCP_APP_MIME_TYPE]},
            }
        },
    }
    unsupported = {
        "capabilities": {
            "extensions": {
                MCP_APPS_EXTENSION_ID: {"mimeTypes": ["text/plain"]},
            }
        }
    }

    assert client_supports_mcp_apps(params) is True
    assert client_supports_mcp_apps(unsupported) is False
    assert client_supports_mcp_apps({}) is False

    server = object.__new__(FuraMCPServer)
    negotiated = server._initialize(params)
    fallback = server._initialize(unsupported)

    assert negotiated["capabilities"]["extensions"] == {
        MCP_APPS_EXTENSION_ID: {"mimeTypes": [MCP_APP_MIME_TYPE]}
    }
    assert "extensions" not in fallback["capabilities"]


def test_gateway_rewrite_preserves_upstream_identity_and_rejects_unsafe_namespaces() -> None:
    canonical = "ui://furatena/catalog-search/v1"

    assert (
        rewrite_mcp_app_uri(
            canonical,
            gateway_authority="gateway.example",
            namespace="team-docs",
        )
        == "ui://gateway.example/team-docs/furatena/catalog-search/v1"
    )
    assert rewrite_mcp_app_uri(
        canonical,
        gateway_authority="gateway.example",
        namespace="product-docs",
    ) != rewrite_mcp_app_uri(
        canonical,
        gateway_authority="gateway.example",
        namespace="team-docs",
    )

    with pytest.raises(MCPAppContractError, match="namespace"):
        rewrite_mcp_app_uri(
            canonical,
            gateway_authority="gateway.example",
            namespace="../collision",
        )
    with pytest.raises(MCPAppContractError, match="stable URI segments"):
        validate_canonical_app_uri("ui://furatena/Catalog-Search/v1")


def test_public_fixture_has_a_complete_deny_by_default_app_contract() -> None:
    apps = _apps_fixture("public")

    errors, warnings = check_mcp_app_contracts(apps["resources"], apps["tools"])

    assert errors == []
    assert warnings == []
    assert apps["gateway"]["collision"] == "reject"
    assert apps["access"] == {
        "audience": "public",
        "redaction": "public-only",
        "trusted_resource_default": "deny",
    }


def test_trusted_author_fixture_does_not_speculate_about_authoring_apps() -> None:
    apps = _apps_fixture("trusted-author")

    assert apps["access"]["inherits_public_resources"] is True
    assert apps["access"]["trusted_resource_default"] == "deny"
    assert apps["resources"] == []
    assert apps["tools"] == []
    assert apps["webmcp"] == "out-of-scope"


def test_agent_lint_reports_missing_stale_unsafe_and_unreachable_app_metadata() -> None:
    apps = copy.deepcopy(_apps_fixture("public"))
    resource = apps["resources"][0]
    tool = apps["tools"][0]
    resource["_meta"]["ui"]["csp"]["connectDomains"] = ["http://unsafe.example"]
    del resource["_meta"]["ui"]["csp"]["frameDomains"]
    resource["_meta"]["io.furatena/mcp-app"]["specVersion"] = "draft"
    tool["_meta"]["ui"]["resourceUri"] = "ui://furatena/missing/v1"
    tool["_meta"]["ui/resourceUri"] = resource["uri"]

    errors, warnings = check_mcp_app_contracts(apps["resources"], apps["tools"])
    rule_ids = {finding.rule_id for finding in errors}

    assert {
        "fura.agent.mcp_app.missing_metadata",
        "fura.agent.mcp_app.stale_contract",
        "fura.agent.mcp_app.unsafe_metadata",
        "fura.agent.mcp_app.unreachable_resource",
    } <= rule_ids
    assert [finding.rule_id for finding in warnings] == ["fura.agent.mcp_app.unreachable_resource"]


def test_agent_lint_rejects_duplicate_resources_and_malformed_csp_origins() -> None:
    apps = copy.deepcopy(_apps_fixture("public"))
    resource = apps["resources"][0]
    resource["_meta"]["ui"]["csp"]["connectDomains"] = [
        "https://exa*mple.com",
        "https://example.com:not-a-port",
    ]
    apps["resources"].append(copy.deepcopy(resource))

    errors, _ = check_mcp_app_contracts(apps["resources"], apps["tools"])

    messages = [finding.message for finding in errors]
    assert any("declared more than once" in message for message in messages)
    assert sum("unsafe CSP origin" in message for message in messages) == 4


def test_trusted_author_resource_is_forbidden_from_public_discovery() -> None:
    apps = copy.deepcopy(_apps_fixture("public"))
    resource = apps["resources"][0]
    contract = resource["_meta"]["io.furatena/mcp-app"]
    contract["audience"] = "trusted-author"
    contract["redaction"] = "session-authorized"

    public_errors, _ = check_mcp_app_contracts(apps["resources"], apps["tools"])
    trusted_errors, _ = check_mcp_app_contracts(
        apps["resources"],
        apps["tools"],
        allow_trusted_author=True,
    )

    assert any("public session" in finding.message for finding in public_errors)
    assert trusted_errors == []
