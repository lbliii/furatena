"""Agent-facing contract lint for MCP and catalog export metadata."""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from furatena.catalog.mcp_apps import (
    FURATENA_APP_META_KEY,
    MCP_APP_MIME_TYPE,
    MCP_APPS_CONTRACT_VERSION,
    MCP_APPS_SPEC_VERSION,
    MCPAppContractError,
    rewrite_mcp_app_uri,
    validate_canonical_app_uri,
)

_MCP_APP_HOST_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\Z")


@dataclass(frozen=True, slots=True)
class AgentLintFinding:
    """One machine-readable agent surface lint finding."""

    severity: str
    message: str
    rule_id: str
    target: str
    next_action: str


_MUTATING_TOOLS = {
    "author_create_draft",
    "author_apply_edit",
    "author_publish",
    "author_unpublish",
}
_READ_ONLY_TOOLS = {
    "semantic_search",
    "retrieve_node",
    "traverse_graph",
    "inspect_source_health",
    "run_checks",
    "explain_stale_impact",
    "author_read_source",
    "author_propose_edit",
    "author_validate",
    "author_inspect_publication_impact",
}
_VAGUE_TERMS = {"thing", "stuff", "misc", "various", "etc."}


def check_agent_contracts(server: Any) -> tuple[list[AgentLintFinding], list[AgentLintFinding]]:
    """Validate agent-facing MCP resources, tools, and export descriptions."""
    errors: list[AgentLintFinding] = []
    warnings: list[AgentLintFinding] = []
    resources = list(server.list_resources())
    tools = list(server.list_tools())

    errors.extend(_duplicate_findings(resources, key="uri", surface="MCP resource"))
    errors.extend(_duplicate_findings(tools, key="name", surface="MCP tool"))

    for resource in resources:
        warnings.extend(_lint_resource(resource))
    for tool in tools:
        tool_errors, tool_warnings = _lint_tool(tool)
        errors.extend(tool_errors)
        warnings.extend(tool_warnings)

    app_errors, app_warnings = check_mcp_app_contracts(
        resources,
        tools,
        allow_trusted_author=bool(getattr(server, "include_private", False)),
    )
    errors.extend(app_errors)
    warnings.extend(app_warnings)

    warnings.extend(_lint_llms_descriptions(server.catalog))
    errors.extend(_lint_milo_surface(server, resources, tools))
    errors.extend(check_agent_manifest_alignment(server))
    return sorted(errors, key=_finding_key), sorted(warnings, key=_finding_key)


def check_mcp_app_contracts(
    resources: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    allow_trusted_author: bool = False,
) -> tuple[list[AgentLintFinding], list[AgentLintFinding]]:
    """Validate versioned MCP Apps resources, links, and security metadata."""
    errors: list[AgentLintFinding] = []
    warnings: list[AgentLintFinding] = []
    app_resources: dict[str, dict[str, Any]] = {}
    linked_uris: set[str] = set()

    for resource in resources:
        uri = str(resource.get("uri") or "")
        meta = resource.get("_meta")
        ui_meta = meta.get("ui") if isinstance(meta, dict) else None
        has_app_meta = isinstance(meta, dict) and (
            ui_meta is not None or FURATENA_APP_META_KEY in meta
        )
        if not uri.startswith("ui://") and not has_app_meta:
            continue
        if not uri.startswith("ui://"):
            errors.append(
                _mcp_app_finding(
                    "unsafe_metadata",
                    f"MCP App metadata is attached to non-ui resource {uri or '<missing-uri>'}",
                    uri or "<missing-uri>",
                    "Move App metadata to a versioned ui:// resource.",
                )
            )
            continue
        if uri in app_resources:
            errors.append(
                _mcp_app_finding(
                    "unsafe_metadata",
                    f"MCP App resource URI {uri} is declared more than once",
                    uri,
                    "Assign every App resource one unique, versioned canonical URI.",
                )
            )
        app_resources[uri] = resource
        errors.extend(
            _lint_mcp_app_resource(
                resource,
                allow_trusted_author=allow_trusted_author,
            )
        )

    for tool in tools:
        name = str(tool.get("name") or "<missing-name>")
        meta = tool.get("_meta")
        if not isinstance(meta, dict):
            continue
        if "ui/resourceUri" in meta:
            errors.append(
                _mcp_app_finding(
                    "stale_contract",
                    f"MCP App tool {name} uses deprecated _meta['ui/resourceUri'] metadata",
                    f"tool:{name}",
                    "Use the stable nested _meta.ui.resourceUri field.",
                )
            )
        ui_meta = meta.get("ui")
        if not isinstance(ui_meta, dict) or "resourceUri" not in ui_meta:
            continue
        uri = str(ui_meta.get("resourceUri") or "")
        linked_uris.add(uri)
        errors.extend(_lint_mcp_app_tool_link(name, uri, ui_meta))
        if uri not in app_resources:
            errors.append(
                _mcp_app_finding(
                    "unreachable_resource",
                    f"MCP App tool {name} links to unavailable resource {uri or '<missing-uri>'}",
                    f"tool:{name}",
                    "Register the linked UI resource or remove the stale tool link.",
                )
            )

    for uri in sorted(set(app_resources) - linked_uris):
        warnings.append(
            _mcp_app_finding(
                "unreachable_resource",
                f"MCP App resource {uri} is not linked from any tool",
                uri,
                "Link the resource from a fallback-capable tool or remove the unreachable resource.",
                severity="warning",
            )
        )

    return sorted(errors, key=_finding_key), sorted(warnings, key=_finding_key)


def check_agent_manifest_alignment(server: Any) -> list[AgentLintFinding]:
    """Cross-check public identities, URLs, versions, and access across agent outputs."""
    from furatena.catalog.channel_manifest import channel_manifest
    from furatena.catalog.deployment_profiles import deployment_profiles_manifest
    from furatena.catalog.export import (
        catalog_graph,
        llms_txt,
        meta_json,
        search_json,
        tools_manifest,
    )
    from furatena.catalog.mcp import SERVER_NAME

    catalog = server.catalog
    config = getattr(server.docs_app, "config", None)
    site_name = str(getattr(getattr(config, "site", None), "name", "Furatena"))
    base_url = str(getattr(server, "base_url", "") or "").rstrip("/")
    base_path = urlsplit(base_url).path.rstrip("/")
    catalog_payload = catalog_graph(catalog, include_private=False)
    search_payload = search_json(catalog, base_url=base_url, include_private=False)
    meta_payload = meta_json(catalog, include_private=False)
    tools_payload = tools_manifest(
        catalog,
        base_url=base_url,
        site_name=site_name,
        include_private=False,
    )
    channels_payload = channel_manifest(
        catalog,
        config=config,
        base_url=base_url,
        mode="live",
    )
    profiles_payload = deployment_profiles_manifest(base_url=base_url)
    initialize = server.handle_request(
        {"jsonrpc": "2.0", "id": "manifest-lint", "method": "initialize", "params": {}}
    )

    findings: list[AgentLintFinding] = []
    version_fields = {
        "catalog.json": catalog_payload.get("schema_version"),
        "search.json": search_payload.get("version"),
        "meta.json": meta_payload.get("schema_version"),
        "tools.json": tools_payload.get("schema_version"),
        "channels.json": channels_payload.get("schema_version"),
        "deployment-profiles.json": profiles_payload.get("schema_version"),
    }
    for surface, version in version_fields.items():
        if not isinstance(version, int) or version < 1:
            findings.append(
                _manifest_finding(
                    f"{surface} has no positive integer schema version",
                    surface,
                    "Publish an explicit positive schema/version field for compatibility checks.",
                )
            )

    initialize_result = initialize.get("result", {}) if isinstance(initialize, dict) else {}
    server_info = initialize_result.get("serverInfo", {})
    if server_info.get("name") != SERVER_NAME or not str(server_info.get("version") or "").strip():
        findings.append(
            _manifest_finding(
                "MCP initialize identity or package version does not match the Furatena server",
                "mcp:initialize",
                "Return the stable MCP server name and installed package version.",
            )
        )
    if not str(initialize_result.get("protocolVersion") or "").strip():
        findings.append(
            _manifest_finding(
                "MCP initialize response has no protocol version",
                "mcp:initialize",
                "Advertise the negotiated MCP protocol version.",
            )
        )

    expected_tool_name = "-".join(part for part in site_name.lower().split() if part) or "furatena"
    if tools_payload.get("name") != f"{expected_tool_name}-docs":
        findings.append(
            _manifest_finding(
                "tools.json identity does not match the configured site name",
                "tools.json:name",
                "Derive the tool manifest identity from site.name.",
            )
        )
    if channels_payload.get("site", {}).get("name") != site_name:
        findings.append(
            _manifest_finding(
                "channels.json site identity does not match the configured site name",
                "channels.json:site",
                "Publish the configured site identity in the channel manifest.",
            )
        )

    catalog_nodes = _manifest_node_index(catalog_payload.get("pages"))
    doc_nodes = {str(node.node_id): _manifest_path(str(node.url)) for node in server._doc_nodes()}
    meta_nodes = _manifest_node_index(meta_payload.get("pages"))
    search_nodes = _manifest_node_index(search_payload.get("entries"), base_path=base_path)
    llms_urls = {
        _manifest_llms_page_path(url) for url in re.findall(r"\]\(([^)]+)\)", llms_txt(catalog))
    }
    mcp_node_ids = {
        unquote(str(resource.get("uri") or "").removeprefix("fura://catalog/nodes/"))
        for resource in server.list_resources()
        if str(resource.get("uri") or "").startswith("fura://catalog/nodes/")
    }

    findings.extend(
        _manifest_set_findings(
            "meta.json",
            expected=set(catalog_nodes),
            actual=set(meta_nodes),
        )
    )
    findings.extend(
        _manifest_set_findings(
            "search.json",
            expected=set(doc_nodes),
            actual=set(search_nodes),
        )
    )
    findings.extend(
        _manifest_set_findings(
            "llms.txt",
            expected=set(doc_nodes.values()),
            actual=llms_urls,
        )
    )
    findings.extend(
        _manifest_set_findings(
            "MCP node resources",
            expected=set(doc_nodes),
            actual=mcp_node_ids,
        )
    )

    counts = {
        "catalog.json": (catalog_payload.get("page_count"), len(catalog_nodes)),
        "search.json": (search_payload.get("page_count"), len(doc_nodes)),
        "meta.json": (meta_payload.get("page_count"), len(catalog_nodes)),
        "tools.json": (tools_payload.get("page_count"), len(catalog_nodes)),
        "channels.json": (channels_payload.get("page_count"), len(catalog_nodes)),
        "MCP node resources": (len(mcp_node_ids), len(doc_nodes)),
    }
    for surface, (count, expected_count) in counts.items():
        if count != expected_count:
            findings.append(
                _manifest_finding(
                    f"{surface} reports {count!r} public pages; expected {expected_count}",
                    f"{surface}:page_count",
                    "Apply the same public export filtering and page identity rules to every agent surface.",
                )
            )

    agent_channel = next(
        (
            channel
            for channel in channels_payload.get("channels", [])
            if channel.get("id") == "agent"
        ),
        {},
    )
    outputs = list(agent_channel.get("outputs") or [])
    advertised_urls = {
        _manifest_path(str(output.get("url") or ""), base_path=base_path)
        for output in outputs
        if output.get("url")
    }
    tool_urls = {
        _manifest_path(str(value), base_path=base_path)
        for key, value in tools_payload.items()
        if key.endswith("_url") and value
    }
    findings.extend(
        _manifest_set_findings(
            "channels.json agent URLs",
            expected=tool_urls,
            actual=advertised_urls,
            allow_extra=True,
        )
    )
    profile_sidecar_urls = {
        _manifest_path(
            str(profiles_payload.get("links", {}).get(key) or ""),
            base_path=base_path,
        )
        for key in ("self", "channels")
    }
    findings.extend(
        _manifest_set_findings(
            "deployment profile sidecar URLs",
            expected=profile_sidecar_urls,
            actual=advertised_urls,
            allow_extra=True,
        )
    )
    from furatena.catalog.route_manifest import route_manifest_entries

    available_routes = {
        entry.path
        for entry in route_manifest_entries(server.docs_app.create_app(), catalog=catalog)
        if "GET" in entry.methods
    }
    profile_urls = {
        _manifest_path(str(value), base_path=base_path)
        for value in profiles_payload.get("links", {}).values()
        if value
    }
    for path in sorted(advertised_urls | profile_urls):
        if not any(_manifest_route_matches(route, path) for route in available_routes):
            findings.append(
                _manifest_finding(
                    f"Advertised agent URL has no GET route in the live delivery profile: {path}",
                    path,
                    "Register the route or remove the stale URL from agent and deployment manifests.",
                )
            )

    for output in outputs:
        output_id = str(output.get("id") or "<missing-id>")
        for field in ("label", "format", "media_type", "visibility"):
            if not str(output.get(field) or "").strip():
                findings.append(
                    _manifest_finding(
                        f"Agent output {output_id} is missing {field}",
                        f"channels.json:{output_id}",
                        "Describe every output's identity, representation, media type, and visibility.",
                    )
                )
        if output.get("visibility") != "public":
            findings.append(
                _manifest_finding(
                    f"Agent output {output_id} is not marked public",
                    f"channels.json:{output_id}",
                    "Keep public channel outputs explicitly public and filter protected content upstream.",
                )
            )

    access = tools_payload.get("access", {})
    if access != {"visibility": "public", "include_private": False}:
        findings.append(
            _manifest_finding(
                "tools.json does not declare the public access-filtering contract",
                "tools.json:access",
                "Declare public visibility with include_private=false for published tools metadata.",
            )
        )
    if bool(getattr(server, "include_private", False)):
        findings.append(
            _manifest_finding(
                "Public manifest lint received an include-private MCP server",
                "mcp:access",
                "Run public manifest validation with include_private disabled.",
            )
        )

    declared_modes = set(profiles_payload.get("agent_modes", {}))
    referenced_modes = {
        str(mode)
        for profile in profiles_payload.get("profiles", [])
        for mode in profile.get("agent_modes", [])
    }
    findings.extend(
        _manifest_set_findings(
            "deployment profile agent modes",
            expected=referenced_modes,
            actual=declared_modes,
            allow_extra=True,
        )
    )
    return sorted(findings, key=_finding_key)


def check_agent_safety(
    server: Any,
    *,
    stale_report: dict[str, Any] | None = None,
) -> tuple[list[AgentLintFinding], list[AgentLintFinding]]:
    """Validate public agent surfaces do not leak private or stale context."""
    import json

    from furatena.catalog.export import (
        catalog_graph,
        llms_full_txt,
        meta_json,
        search_json,
        tools_manifest,
    )
    from furatena.catalog.impact import stale_impact_report
    from furatena.catalog.lifecycle import public_nodes

    catalog = server.catalog
    errors: list[AgentLintFinding] = []
    warnings: list[AgentLintFinding] = []
    public_doc_nodes = public_nodes(catalog.doc_nodes())
    public_node_ids = {node.node_id for node in public_doc_nodes}
    private_nodes = [node for node in catalog.doc_nodes() if node.node_id not in public_node_ids]
    public_surfaces = {
        "catalog": json.dumps(catalog_graph(catalog, include_private=False), sort_keys=True),
        "search": json.dumps(search_json(catalog, include_private=False), sort_keys=True),
        "llms-full": llms_full_txt(catalog, include_private=False),
        "tools": json.dumps(tools_manifest(catalog, include_private=False), sort_keys=True),
        "meta": json.dumps(meta_json(catalog, include_private=False), sort_keys=True),
        "mcp-resources": json.dumps(server.list_resources(), sort_keys=True),
    }

    for node in private_nodes:
        needles = {node.node_id, node.url, node.title}
        for surface, payload in public_surfaces.items():
            if any(needle and needle in payload for needle in needles):
                errors.append(
                    _finding(
                        "error",
                        "fura.agent_safety.private_leak",
                        f"Private node {node.node_id} appears in public {surface} agent surface",
                        f"node:{node.node_id}",
                        "Filter draft/private/internal/archived content from all public agent exports.",
                    )
                )

    report = (
        stale_report
        if stale_report is not None
        else stale_impact_report(catalog, include_private=False)
    )
    for item in report.get("impact", []):
        node_id = str(
            (item.get("provenance") or {}).get("node_id") or item.get("slug") or "unknown"
        )
        source_path = str(
            (item.get("provenance") or {}).get("path") or item.get("source_key") or node_id
        )
        warnings.append(
            _finding(
                "warning",
                "fura.agent_safety.stale_context",
                f"Stale agent context for {node_id} should be refreshed or withheld from retrieval",
                source_path,
                str(
                    item.get("recommended_remediation")
                    or "Refresh affected agent exports before publishing."
                ),
            )
        )

    return sorted(errors, key=_finding_key), sorted(warnings, key=_finding_key)


def _lint_resource(resource: dict[str, Any]) -> list[AgentLintFinding]:
    findings: list[AgentLintFinding] = []
    uri = str(resource.get("uri") or "<missing-uri>")
    for field in ("uri", "name", "description", "mimeType"):
        value = str(resource.get(field) or "").strip()
        if not value:
            findings.append(
                _finding(
                    "warning",
                    "fura.agent.resource_metadata",
                    f"MCP resource {uri} is missing {field}",
                    uri,
                    "Add complete resource metadata for agent resource selection.",
                )
            )
    desc = str(resource.get("description") or "").strip()
    if desc and len(desc) < 12:
        findings.append(
            _finding(
                "warning",
                "fura.agent.description",
                f"MCP resource {uri} has a terse description",
                uri,
                "Describe what the resource contains and when an agent should read it.",
            )
        )
    return findings


def _lint_mcp_app_resource(
    resource: dict[str, Any],
    *,
    allow_trusted_author: bool,
) -> list[AgentLintFinding]:
    findings: list[AgentLintFinding] = []
    uri = str(resource.get("uri") or "<missing-uri>")
    meta = resource.get("_meta")
    if not isinstance(meta, dict):
        return [
            _mcp_app_finding(
                "missing_metadata",
                f"MCP App resource {uri} is missing _meta",
                uri,
                "Declare standard UI security metadata and the Furatena access contract.",
            )
        ]

    ui_meta = meta.get("ui")
    if not isinstance(ui_meta, dict):
        findings.append(
            _mcp_app_finding(
                "missing_metadata",
                f"MCP App resource {uri} is missing _meta.ui security metadata",
                uri,
                "Declare explicit CSP domains and sandbox permissions under _meta.ui.",
            )
        )
    else:
        findings.extend(_lint_mcp_app_security(uri, ui_meta))

    contract_meta = meta.get(FURATENA_APP_META_KEY)
    if not isinstance(contract_meta, dict):
        findings.append(
            _mcp_app_finding(
                "missing_metadata",
                f"MCP App resource {uri} is missing _meta['{FURATENA_APP_META_KEY}']",
                uri,
                "Declare contract version, canonical URI, audience, redaction, and fallback metadata.",
            )
        )
        return findings

    version = contract_meta.get("contractVersion")
    spec_version = contract_meta.get("specVersion")
    canonical_uri = str(contract_meta.get("canonicalUri") or "")
    audience = contract_meta.get("audience")
    redaction = contract_meta.get("redaction")
    fallback = contract_meta.get("fallback")

    required = {
        "contractVersion": version,
        "specVersion": spec_version,
        "canonicalUri": canonical_uri,
        "audience": audience,
        "redaction": redaction,
        "fallback": fallback,
    }
    for field, value in required.items():
        if value in (None, ""):
            findings.append(
                _mcp_app_finding(
                    "missing_metadata",
                    f"MCP App resource {uri} is missing {FURATENA_APP_META_KEY}.{field}",
                    uri,
                    "Complete the versioned Furatena MCP Apps metadata block.",
                )
            )

    if version not in (None, MCP_APPS_CONTRACT_VERSION):
        findings.append(
            _mcp_app_finding(
                "stale_contract",
                f"MCP App resource {uri} declares unsupported contract version {version!r}",
                uri,
                f"Migrate the resource and tool links to contract v{MCP_APPS_CONTRACT_VERSION}.",
            )
        )
    if spec_version not in (None, MCP_APPS_SPEC_VERSION):
        findings.append(
            _mcp_app_finding(
                "stale_contract",
                f"MCP App resource {uri} declares unsupported Apps spec {spec_version!r}",
                uri,
                f"Review and declare MCP Apps spec {MCP_APPS_SPEC_VERSION}.",
            )
        )

    if canonical_uri:
        try:
            validate_canonical_app_uri(canonical_uri)
        except (MCPAppContractError, ValueError) as exc:
            findings.append(
                _mcp_app_finding(
                    "unsafe_metadata",
                    f"MCP App resource {uri} has invalid canonical URI: {exc}",
                    uri,
                    "Use a stable versioned canonical ui:// URI without credentials or selectors.",
                )
            )
        if canonical_uri != uri:
            gateway = contract_meta.get("gateway")
            expected = ""
            if isinstance(gateway, dict):
                with suppress(MCPAppContractError, ValueError):
                    expected = rewrite_mcp_app_uri(
                        canonical_uri,
                        gateway_authority=str(gateway.get("authority") or ""),
                        namespace=str(gateway.get("namespace") or ""),
                    )
            if expected != uri:
                findings.append(
                    _mcp_app_finding(
                        "unsafe_metadata",
                        f"MCP App resource {uri} is not a valid rewrite of {canonical_uri}",
                        uri,
                        "Rewrite the resource and every tool link with one collision-free gateway namespace.",
                    )
                )

    valid_redaction = {
        "public": "public-only",
        "trusted-author": "session-authorized",
    }
    if audience not in (None, *valid_redaction):
        findings.append(
            _mcp_app_finding(
                "unsafe_metadata",
                f"MCP App resource {uri} has unknown audience {audience!r}",
                uri,
                "Use the public or trusted-author audience.",
            )
        )
    elif audience in valid_redaction and redaction not in (None, valid_redaction[audience]):
        findings.append(
            _mcp_app_finding(
                "unsafe_metadata",
                f"MCP App resource {uri} has redaction {redaction!r} for {audience} audience",
                uri,
                f"Use redaction={valid_redaction[audience]!r} for this audience.",
            )
        )
    if audience == "trusted-author" and not allow_trusted_author:
        findings.append(
            _mcp_app_finding(
                "unsafe_metadata",
                f"Trusted-author MCP App resource {uri} is exposed by a public session",
                uri,
                "Filter trusted-author App resources before public resource discovery.",
            )
        )
    if fallback not in (None, "structured-content"):
        findings.append(
            _mcp_app_finding(
                "unsafe_metadata",
                f"MCP App resource {uri} declares unsupported fallback {fallback!r}",
                uri,
                "Use the ordinary structured-content tool result as the non-App fallback.",
            )
        )
    if resource.get("mimeType") != MCP_APP_MIME_TYPE:
        findings.append(
            _mcp_app_finding(
                "unsafe_metadata",
                f"MCP App resource {uri} must use MIME type {MCP_APP_MIME_TYPE}",
                uri,
                "Serve bundled HTML with the stable MCP Apps MIME profile.",
            )
        )
    return findings


def _lint_mcp_app_security(uri: str, ui_meta: dict[str, Any]) -> list[AgentLintFinding]:
    findings: list[AgentLintFinding] = []
    csp = ui_meta.get("csp")
    if not isinstance(csp, dict):
        return [
            _mcp_app_finding(
                "missing_metadata",
                f"MCP App resource {uri} is missing explicit _meta.ui.csp",
                uri,
                "Declare all four CSP domain arrays; use empty arrays for deny-by-default.",
            )
        ]
    domain_fields = {
        "connectDomains": {"https", "wss"},
        "resourceDomains": {"https"},
        "frameDomains": {"https"},
        "baseUriDomains": {"https"},
    }
    for field, schemes in domain_fields.items():
        origins = csp.get(field)
        if not isinstance(origins, list):
            findings.append(
                _mcp_app_finding(
                    "missing_metadata",
                    f"MCP App resource {uri} CSP is missing array {field}",
                    uri,
                    "Declare explicit CSP arrays; use an empty array to deny that capability.",
                )
            )
            continue
        for origin in origins:
            if not _safe_mcp_app_origin(origin, schemes=schemes):
                findings.append(
                    _mcp_app_finding(
                        "unsafe_metadata",
                        f"MCP App resource {uri} has unsafe CSP origin {origin!r} in {field}",
                        uri,
                        "Use exact secure origins without credentials, paths, queries, or fragments.",
                    )
                )
    permissions = ui_meta.get("permissions", {})
    allowed_permissions = {"camera", "microphone", "geolocation", "clipboardWrite"}
    if not isinstance(permissions, dict) or any(
        permission not in allowed_permissions or value != {}
        for permission, value in permissions.items()
    ):
        findings.append(
            _mcp_app_finding(
                "unsafe_metadata",
                f"MCP App resource {uri} declares invalid sandbox permissions",
                uri,
                "Request only standard MCP Apps permissions with empty-object values.",
            )
        )
    domain = ui_meta.get("domain")
    if domain is not None and not _safe_mcp_app_origin(domain, schemes={"https"}):
        findings.append(
            _mcp_app_finding(
                "unsafe_metadata",
                f"MCP App resource {uri} has unsafe dedicated domain {domain!r}",
                uri,
                "Use an exact HTTPS origin for a dedicated App domain.",
            )
        )
    return findings


def _lint_mcp_app_tool_link(
    name: str,
    uri: str,
    ui_meta: dict[str, Any],
) -> list[AgentLintFinding]:
    findings: list[AgentLintFinding] = []
    if not uri.startswith("ui://"):
        findings.append(
            _mcp_app_finding(
                "unsafe_metadata",
                f"MCP App tool {name} links to non-ui resource {uri or '<missing-uri>'}",
                f"tool:{name}",
                "Link the tool to a versioned ui:// resource.",
            )
        )
    visibility = ui_meta.get("visibility")
    if (
        not isinstance(visibility, list)
        or not all(isinstance(item, str) for item in visibility)
        or set(visibility) != {"model", "app"}
    ):
        findings.append(
            _mcp_app_finding(
                "missing_metadata",
                f"MCP App tool {name} must explicitly allow model and app visibility",
                f"tool:{name}",
                "Set _meta.ui.visibility to ['model', 'app'] so fallback and App calls share one tool.",
            )
        )
    return findings


def _safe_mcp_app_origin(value: Any, *, schemes: set[str]) -> bool:
    if not isinstance(value, str) or value == "*":
        return False
    wildcard = any(value.startswith(f"{scheme}://*.") for scheme in schemes)
    if "*" in value and (not wildcard or value.count("*") != 1):
        return False
    candidate = value.replace("://*.", "://wildcard.", 1)
    parsed = urlsplit(candidate)
    if parsed.scheme not in schemes or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    hostname = parsed.hostname
    labels = hostname.split(".")
    if port is not None and not 1 <= port <= 65535:
        return False
    if any(not _MCP_APP_HOST_LABEL_RE.fullmatch(label) for label in labels):
        return False
    return parsed.path in ("", "/") and not parsed.query and not parsed.fragment


def _lint_tool(tool: dict[str, Any]) -> tuple[list[AgentLintFinding], list[AgentLintFinding]]:
    errors: list[AgentLintFinding] = []
    warnings: list[AgentLintFinding] = []
    name = str(tool.get("name") or "<missing-name>")
    desc = str(tool.get("description") or "").strip()
    target = f"tool:{name}"

    if not desc:
        errors.append(
            _finding(
                "error",
                "fura.agent.tool_description",
                f"MCP tool {name} is missing a description",
                target,
                "Add a concise description with action boundaries and safety behavior.",
            )
        )
    else:
        warnings.extend(_lint_description(desc, target, f"MCP tool {name}"))

    input_schema = tool.get("inputSchema")
    if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
        errors.append(
            _finding(
                "error",
                "fura.agent.input_schema",
                f"MCP tool {name} is missing an object inputSchema",
                target,
                "Declare an object input schema with typed parameters.",
            )
        )
    else:
        errors.extend(_lint_input_schema(name, input_schema))

    output_schema = tool.get("outputSchema")
    if not isinstance(output_schema, dict) or output_schema.get("type") != "object":
        errors.append(
            _finding(
                "error",
                "fura.agent.output_schema",
                f"MCP tool {name} is missing an object outputSchema",
                target,
                "Declare a structured object output schema for tool results.",
            )
        )
    elif not output_schema.get("required"):
        warnings.append(
            _finding(
                "warning",
                "fura.agent.output_schema",
                f"MCP tool {name} outputSchema has no required result keys",
                target,
                "List stable top-level result fields in outputSchema.required.",
            )
        )

    if name in _MUTATING_TOOLS:
        lower = desc.lower()
        if "dry-run" not in lower or "confirmed=true" not in lower:
            errors.append(
                _finding(
                    "error",
                    "fura.agent.mutation_boundary",
                    f"MCP tool {name} does not clearly describe dry-run and confirmation requirements",
                    target,
                    "State that writes default to dry-run and require confirmed=true.",
                )
            )
    elif name in _READ_ONLY_TOOLS and name.startswith("author_") and "read" not in name:
        if (
            "inspect" not in desc.lower()
            and "preview" not in desc.lower()
            and "validation" not in desc.lower()
        ):
            warnings.append(
                _finding(
                    "warning",
                    "fura.agent.action_boundary",
                    f"MCP tool {name} should make its non-mutating boundary explicit",
                    target,
                    "Use wording such as inspect, preview, or validate for read-only authoring tools.",
                )
            )

    if (
        name.startswith("author_")
        and name != "author_propose_edit"
        and "include-private" not in desc.lower()
        and name == "author_read_source"
    ):
        errors.append(
            _finding(
                "error",
                "fura.agent.permission_note",
                f"MCP tool {name} is missing the include-private permission note",
                target,
                "Document that source reads require an include-private author MCP session.",
            )
        )

    return errors, warnings


def _lint_input_schema(name: str, schema: dict[str, Any]) -> list[AgentLintFinding]:
    findings: list[AgentLintFinding] = []
    target = f"tool:{name}"
    properties = schema.get("properties")
    if properties is None:
        findings.append(
            _finding(
                "error",
                "fura.agent.input_schema",
                f"MCP tool {name} inputSchema is missing properties",
                target,
                "Declare properties, even when the tool takes no arguments.",
            )
        )
        return findings
    if not isinstance(properties, dict):
        findings.append(
            _finding(
                "error",
                "fura.agent.input_schema",
                f"MCP tool {name} inputSchema properties is not an object",
                target,
                "Use a JSON Schema object for inputSchema.properties.",
            )
        )
        return findings
    required = schema.get("required") or []
    if not isinstance(required, list):
        findings.append(
            _finding(
                "error",
                "fura.agent.input_schema",
                f"MCP tool {name} inputSchema required is not a list",
                target,
                "Use a JSON Schema required array.",
            )
        )
    for param, param_schema in properties.items():
        if not isinstance(param_schema, dict):
            findings.append(
                _finding(
                    "error",
                    "fura.agent.input_schema",
                    f"MCP tool {name} parameter {param} schema is not an object",
                    target,
                    "Use a JSON Schema object for each parameter.",
                )
            )
            continue
        if "type" not in param_schema and "enum" not in param_schema:
            findings.append(
                _finding(
                    "error",
                    "fura.agent.input_schema",
                    f"MCP tool {name} parameter {param} has no type or enum",
                    target,
                    "Declare parameter types so agents can form valid calls.",
                )
            )
        if not str(param_schema.get("description") or "").strip():
            findings.append(
                _finding(
                    "error",
                    "fura.agent.parameter_description",
                    f"MCP tool {name} parameter {param} is missing a description",
                    target,
                    "Describe parameter semantics and valid values.",
                )
            )
    return findings


def _lint_llms_descriptions(catalog: Any) -> list[AgentLintFinding]:
    findings: list[AgentLintFinding] = []
    for node in catalog.doc_nodes():
        if node.meta.get("draft") or node.meta.get("visibility") in {
            "private",
            "internal",
            "archived",
        }:
            continue
        description = str(node.description or "").strip()
        if description:
            if len(description) > 240:
                findings.append(
                    _finding(
                        "warning",
                        "fura.agent.llms_description",
                        f"Catalog node {node.node_id} description is longer than 240 characters",
                        f"node:{node.node_id}",
                        "Shorten front matter description so agents can compare llms/search entries quickly.",
                    )
                )
            continue
        findings.append(
            _finding(
                "warning",
                "fura.agent.llms_description",
                f"Catalog node {node.node_id} has no description for llms/search exports",
                f"node:{node.node_id}",
                "Add front matter description so agents can rank llms/search entries.",
            )
        )
    return findings


def _lint_milo_surface(
    server: Any,
    resources: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> list[AgentLintFinding]:
    try:
        from milo.testing import MCPClient

        from furatena.catalog.mcp import build_milo_cli
    except Exception as exc:  # pragma: no cover - defensive optional dependency path
        return [
            _finding(
                "error",
                "fura.agent.milo",
                f"Milo MCP testing surface is unavailable: {exc}",
                "milo",
                "Install milo-cli or fix the Milo MCP adapter dependency.",
            )
        ]

    client = MCPClient(build_milo_cli(server))
    milo_resources = client.list_resources()
    milo_tools = client.list_tools()
    resource_uris = {str(resource.get("uri") or "") for resource in resources}
    milo_uris = {str(resource.get("uri") or "") for resource in milo_resources}
    tool_names = {str(tool.get("name") or "") for tool in tools}
    milo_tool_names = {str(getattr(tool, "name", "")) for tool in milo_tools}

    findings: list[AgentLintFinding] = []
    missing_resources = sorted(resource_uris - milo_uris)
    if missing_resources:
        findings.append(
            _finding(
                "error",
                "fura.agent.milo",
                f"Milo MCP adapter is missing {len(missing_resources)} Furatena resource(s)",
                "milo:resources",
                "Register every Furatena MCP resource in build_milo_cli.",
            )
        )
    missing_tools = sorted(tool_names - milo_tool_names)
    if missing_tools:
        findings.append(
            _finding(
                "error",
                "fura.agent.milo",
                f"Milo MCP adapter is missing {len(missing_tools)} Furatena tool(s)",
                "milo:tools",
                "Register every Furatena MCP tool in build_milo_cli.",
            )
        )
    return findings


def _lint_description(desc: str, target: str, label: str) -> list[AgentLintFinding]:
    findings: list[AgentLintFinding] = []
    lower = desc.lower()
    if len(desc) < 18:
        findings.append(
            _finding(
                "warning",
                "fura.agent.description",
                f"{label} description is too terse for reliable agent selection",
                target,
                "Use a concise sentence that names the content or action boundary.",
            )
        )
    if len(desc) > 240:
        findings.append(
            _finding(
                "warning",
                "fura.agent.description",
                f"{label} description is longer than 240 characters",
                target,
                "Shorten descriptions so agents can compare tools and resources quickly.",
            )
        )
    if desc[-1:] not in {".", "!", "?"}:
        findings.append(
            _finding(
                "warning",
                "fura.agent.description",
                f"{label} description should end with punctuation",
                target,
                "Use a complete sentence for agent-facing descriptions.",
            )
        )
    vague = sorted(term for term in _VAGUE_TERMS if term in lower)
    if vague:
        findings.append(
            _finding(
                "warning",
                "fura.agent.description",
                f"{label} description contains vague term(s): {', '.join(vague)}",
                target,
                "Replace vague wording with concrete content or action boundaries.",
            )
        )
    return findings


def _duplicate_findings(
    records: list[dict[str, Any]],
    *,
    key: str,
    surface: str,
) -> list[AgentLintFinding]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for record in records:
        value = str(record.get(key) or "").strip()
        if not value:
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return [
        _finding(
            "error",
            "fura.agent.duplicate",
            f"Duplicate {surface} {key}: {value}",
            value,
            "Use stable unique tool names and resource URIs.",
        )
        for value in sorted(duplicates)
    ]


def _manifest_node_index(records: Any, *, base_path: str = "") -> dict[str, str]:
    if not isinstance(records, list):
        return {}
    return {
        str(record.get("node_id")): _manifest_path(
            str(record.get("url") or ""),
            base_path=base_path,
        )
        for record in records
        if isinstance(record, dict) and record.get("node_id") and record.get("url")
    }


def _manifest_path(value: str, *, base_path: str = "") -> str:
    path = urlsplit(value).path or value
    if base_path and (path == base_path or path.startswith(f"{base_path}/")):
        path = path.removeprefix(base_path) or "/"
    return path if path.startswith("/") else f"/{path}"


def _manifest_llms_page_path(value: str) -> str:
    """Normalize supported markdown aliases to their catalog page URL."""
    path = _manifest_path(value)
    if path == "/index.md":
        return "/"
    if path.endswith("/index.md"):
        return f"{path[: -len('index.md')]}"
    if path.endswith(".md"):
        return f"{path[: -len('.md')]}/"
    return path


def _manifest_route_matches(route: str, path: str) -> bool:
    parts = re.split(r"(\{[^}]+\})", route)
    pattern = "".join(r"[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
    return re.fullmatch(pattern, path) is not None


def _manifest_set_findings(
    surface: str,
    *,
    expected: set[str],
    actual: set[str],
    allow_extra: bool = False,
) -> list[AgentLintFinding]:
    findings: list[AgentLintFinding] = []
    missing = sorted(expected - actual)
    unexpected = [] if allow_extra else sorted(actual - expected)
    if missing:
        findings.append(
            _manifest_finding(
                f"{surface} is missing {len(missing)} expected identity or URL value(s): "
                f"{', '.join(missing[:3])}",
                surface,
                "Generate every agent surface from the same public catalog and URL registry.",
            )
        )
    if unexpected:
        findings.append(
            _manifest_finding(
                f"{surface} has {len(unexpected)} unexpected identity or URL value(s): "
                f"{', '.join(unexpected[:3])}",
                surface,
                "Remove stale or access-ineligible entries from the agent surface.",
            )
        )
    return findings


def _manifest_finding(message: str, target: str, next_action: str) -> AgentLintFinding:
    return _finding(
        "error",
        "fura.agent.manifest_alignment",
        message,
        target,
        next_action,
    )


def _mcp_app_finding(
    kind: str,
    message: str,
    target: str,
    next_action: str,
    *,
    severity: str = "error",
) -> AgentLintFinding:
    return _finding(
        severity,
        f"fura.agent.mcp_app.{kind}",
        message,
        target,
        next_action,
    )


def _finding(
    severity: str,
    rule_id: str,
    message: str,
    target: str,
    next_action: str,
) -> AgentLintFinding:
    return AgentLintFinding(
        severity=severity,
        message=message,
        rule_id=rule_id,
        target=target,
        next_action=next_action,
    )


def _finding_key(finding: AgentLintFinding) -> tuple[str, str, str]:
    return finding.rule_id, finding.target, finding.message
