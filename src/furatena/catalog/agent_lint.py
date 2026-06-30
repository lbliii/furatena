"""Agent-facing contract lint for MCP and catalog export metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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

    warnings.extend(_lint_llms_descriptions(server.catalog))
    errors.extend(_lint_milo_surface(server, resources, tools))
    return sorted(errors, key=_finding_key), sorted(warnings, key=_finding_key)


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

    report = stale_report if stale_report is not None else stale_impact_report(catalog, include_private=False)
    for item in report.get("impact", []):
        node_id = str((item.get("provenance") or {}).get("node_id") or item.get("slug") or "unknown")
        source_path = str((item.get("provenance") or {}).get("path") or item.get("source_key") or node_id)
        warnings.append(
            _finding(
                "warning",
                "fura.agent_safety.stale_context",
                f"Stale agent context for {node_id} should be refreshed or withheld from retrieval",
                source_path,
                str(item.get("recommended_remediation") or "Refresh affected agent exports before publishing."),
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
        if "inspect" not in desc.lower() and "preview" not in desc.lower() and "validation" not in desc.lower():
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
        if node.meta.get("draft") or node.meta.get("visibility") in {"private", "internal", "archived"}:
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
