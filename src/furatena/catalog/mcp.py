"""Minimal MCP server for exposing a Furatena catalog to local agents."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from furatena.catalog.check import check_catalog
from furatena.catalog.export import catalog_graph
from furatena.catalog.inventories.export import inventories_json
from furatena.catalog.lifecycle import public_nodes
from furatena.catalog.query import query_catalog_graph
from furatena.catalog.registry import load_mounts
from furatena.catalog.semantic import hybrid_search, retrieve_node
from furatena.catalog.structure_index import build_structure_index
from furatena.cli.authoring import (
    AuthorOperationResult,
    author_apply_edit,
    author_new,
    author_read_source,
    author_status,
    author_transition,
    author_validate,
)

MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "furatena-catalog"
_SENSITIVE_TOOLS = {
    "author_create_draft",
    "author_read_source",
    "author_propose_edit",
    "author_apply_edit",
    "author_validate",
    "author_publish",
    "author_unpublish",
    "author_archive",
    "author_inspect_publication_impact",
}
_TOKEN_KEYS = {"token", "privileged_token", "authorization", "api_key"}


@dataclass(frozen=True, slots=True)
class MCPAccessPolicy:
    """Permissions and safety bounds for local or remote MCP sessions."""

    transport: str = "local"
    actor: str = "mcp-local"
    tenant: str | None = None
    site: str | None = None
    allow_private: bool = False
    privileged_tokens: frozenset[str] = field(default_factory=frozenset)
    rate_limit_per_minute: int = 120
    timeout_seconds: float = 15.0
    max_output_chars: int = 200_000

    @property
    def remote(self) -> bool:
        return self.transport == "remote"

    @property
    def requires_privileged_token(self) -> bool:
        return self.remote and bool(self.privileged_tokens)

    def has_privileged_token(self, arguments: dict[str, Any]) -> bool:
        token = _optional_str(arguments.get("privileged_token")) or _optional_str(arguments.get("token"))
        return bool(token and token in self.privileged_tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "actor": self.actor,
            "tenant": self.tenant,
            "site": self.site,
            "allow_private": self.allow_private,
            "requires_privileged_token": self.requires_privileged_token,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "timeout_seconds": self.timeout_seconds,
            "max_output_chars": self.max_output_chars,
        }


class MCPError(Exception):
    """JSON-RPC error with an MCP-compatible code and payload."""

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class FuraMCPServer:
    """Dependency-free JSON-RPC handler for Furatena MCP resources and tools."""

    def __init__(
        self,
        docs_app: Any,
        *,
        base_url: str = "",
        include_private: bool = False,
        policy: MCPAccessPolicy | None = None,
    ) -> None:
        self.docs_app = docs_app
        self.catalog = docs_app.catalog
        self.embedding_index = docs_app.embedding_index
        self.base_url = base_url.rstrip("/")
        self.policy = policy or MCPAccessPolicy(allow_private=include_private)
        self.include_private = include_private and self.policy.allow_private
        self.audit_log: list[dict[str, Any]] = []
        self._rate_window_started = time.monotonic()
        self._rate_count = 0

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Handle one JSON-RPC request object.

        Notifications have no ``id`` and intentionally return ``None``.
        """
        request_id = request.get("id")
        method = str(request.get("method") or "")
        params = request.get("params") or {}
        try:
            if method == "initialize":
                return self._response(request_id, self._initialize(params))
            if method == "notifications/initialized":
                return None
            if method == "ping":
                return self._response(request_id, {})
            if method == "resources/list":
                return self._response(request_id, {"resources": self.list_resources()})
            if method == "resources/read":
                uri = str(params.get("uri") or "")
                return self._response(request_id, {"contents": [self.read_resource(uri)]})
            if method == "tools/list":
                return self._response(request_id, {"tools": self.list_tools()})
            if method == "tools/call":
                return self._response(
                    request_id,
                    self.call_tool(
                        str(params.get("name") or ""),
                        params.get("arguments") or {},
                    ),
                )
            raise MCPError(-32601, f"unknown method: {method}")
        except MCPError as exc:
            return self._error_response(request_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive transport boundary
            return self._error_response(request_id, -32603, str(exc))

    def list_resources(self) -> list[dict[str, Any]]:
        resources = [
            _resource("fura://catalog/nodes", "Catalog nodes", "All catalog page nodes."),
            _resource(
                "fura://catalog/graph",
                "Catalog graph",
                "Full DCP graph projection with pages, edges, graph nodes, and namespaces.",
            ),
            _resource(
                "fura://catalog/api-operations",
                "API operations",
                "Autodoc/API catalog nodes exposed as operation records.",
            ),
            _resource(
                "fura://catalog/structure",
                "Structure index",
                "Headings and directives extracted from Content IR.",
            ),
            _resource(
                "fura://catalog/inventories",
                "Reference inventories",
                "Machine-readable reference inventory manifest.",
            ),
            _resource(
                "fura://catalog/sources",
                "Source manifests",
                "Configured catalog mounts and source health.",
            ),
            _resource(
                "fura://catalog/channels",
                "Channel manifests",
                "Version channel metadata for configured mounts.",
            ),
            _resource(
                "fura://reports/validation",
                "Validation report",
                "Content, link, schema, theme, and view validation diagnostics.",
            ),
            _resource(
                "fura://reports/stale-impact",
                "Stale impact report",
                "Author-mode stale entries and impacted update targets.",
            ),
            _resource(
                "fura://reports/audit",
                "MCP audit report",
                "Sanitized MCP tool audit log with actor, tenant, site, inputs, and result status.",
            ),
        ]
        resources.extend(
            _resource(
                _node_uri(node.node_id),
                node.title,
                node.description or node.url,
            )
            for node in self._doc_nodes()
        )
        return resources

    def read_resource(self, uri: str) -> dict[str, Any]:
        payload = self._resource_payload(uri)
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": _dumps(payload),
        }

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "semantic_search",
                "description": "Hybrid keyword + semantic search over catalog pages and chunks.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": _string_schema("Natural language or keyword query to search for."),
                        "limit": _integer_schema(
                            "Maximum number of search results to return.",
                            default=12,
                            minimum=1,
                            maximum=50,
                        ),
                        "mount": _string_schema("Optional mount id used to scope search results."),
                        "edition": _string_schema("Optional edition id used to scope search results."),
                    },
                    "required": ["query"],
                },
                "outputSchema": _object_schema("query", "count", "results"),
            },
            {
                "name": "retrieve_node",
                "description": "Retrieve a catalog node with chunks, backlinks, and similar pages.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "node_id": _string_schema("Stable catalog node id to retrieve."),
                    },
                    "required": ["node_id"],
                },
                "outputSchema": _object_schema("node_id", "chunks", "backlinks", "api_operation"),
            },
            {
                "name": "query_graph",
                "description": "Filter the DCP catalog graph by page metadata and edge source, target, or kind.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "mount": _string_schema("Optional mount id used to scope graph source pages."),
                        "tag": _string_schema("Optional page tag used to scope graph source pages."),
                        "format": _string_schema(
                            "Optional content format, source, or source kind used to scope pages."
                        ),
                        "owner": _string_schema("Optional owner or team metadata used to scope pages."),
                        "team": _string_schema("Alias for owner when callers use team metadata."),
                        "locale": _string_schema("Optional locale or language code used to scope pages."),
                        "lang": _string_schema("Alias for locale when callers use language metadata."),
                        "edge_kind": _string_schema("Optional graph edge kind such as link, api_schema, or owned_by."),
                        "edge": _string_schema("Alias for edge_kind."),
                        "kind": _string_schema("Alias for edge_kind."),
                        "link_edge": _string_schema("Alias for edge_kind."),
                        "source": _string_schema("Optional edge source node id, slug, or URL."),
                        "from": _string_schema("Alias for source."),
                        "linked_from": _string_schema("Alias for source."),
                        "target": _string_schema("Optional edge target node id, slug, URL, or external graph id."),
                        "to": _string_schema("Alias for target."),
                        "linked_to": _string_schema("Alias for target."),
                        "include_private": _boolean_schema(
                            "Include private nodes only when this MCP session allows private content.",
                        ),
                    },
                },
                "outputSchema": _object_schema("query", "page_count", "edge_count", "pages", "edges", "graph_nodes"),
            },
            {
                "name": "traverse_graph",
                "description": "Traverse backlinks, child pages, outbound links, or neighboring pages.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "node_id": _string_schema("Stable catalog node id to traverse."),
                        "url": _string_schema("Catalog URL to traverse when node_id is not provided."),
                        "direction": {
                            "type": "string",
                            "enum": ["neighbors", "backlinks", "children", "outbound"],
                            "default": "neighbors",
                            "description": "Graph direction to inspect from the selected node.",
                        },
                        "limit": _integer_schema(
                            "Maximum number of graph records to return.",
                            default=20,
                            minimum=1,
                            maximum=100,
                        ),
                    },
                },
                "outputSchema": _object_schema("node", "direction", "results"),
            },
            {
                "name": "inspect_source_health",
                "description": "Inspect source roots, mount health, and channel coverage.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "mount": _string_schema("Optional mount id used to scope the health report."),
                    },
                },
                "outputSchema": _object_schema("mount_count", "mounts"),
            },
            {
                "name": "run_checks",
                "description": "Run Furatena content, link, schema, theme, and view checks.",
                "inputSchema": {"type": "object", "properties": {}},
                "outputSchema": _object_schema("ok", "errors", "warnings"),
            },
            {
                "name": "explain_stale_impact",
                "description": "Explain author-mode stale entries and impacted refresh targets.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "slug": _string_schema("Optional slug used to scope stale-impact entries."),
                    },
                },
                "outputSchema": _object_schema("stale_count", "entries"),
            },
            {
                "name": "author_create_draft",
                "description": "Create a draft source file. Defaults to dry-run and requires confirmed=true to write.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "slug": _string_schema("New draft slug relative to the target mount."),
                        "title": _string_schema("Optional page title for the new draft front matter."),
                        "mount": _string_schema("Optional mount id where the draft should be created."),
                        "dry_run": _boolean_schema("When true, preview the write without changing files.", default=True),
                        "confirmed": _boolean_schema(
                            "Must be true with dry_run=false before files are written.",
                            default=False,
                        ),
                        "actor": _string_schema("Optional actor id stored in the audit payload."),
                        "privileged_token": _privileged_token_schema(),
                    },
                    "required": ["slug"],
                },
                "outputSchema": _object_schema("operation_id", "ok", "audit"),
            },
            {
                "name": "author_read_source",
                "description": "Read a source file for authoring. Requires an include-private author MCP session.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target": _string_schema("Existing source slug or path to read."),
                        "mount": _string_schema("Optional mount id used to disambiguate the target."),
                        "actor": _string_schema("Optional actor id stored in the audit payload."),
                        "privileged_token": _privileged_token_schema(),
                    },
                    "required": ["target"],
                },
                "outputSchema": _object_schema("operation_id", "ok", "source", "audit"),
            },
            {
                "name": "author_propose_edit",
                "description": "Preview an exact-text source edit without writing files.",
                "inputSchema": _author_edit_schema(),
                "outputSchema": _object_schema("operation_id", "ok", "diff", "audit"),
            },
            {
                "name": "author_apply_edit",
                "description": "Apply an exact-text source edit. Defaults to dry-run and requires confirmed=true to write.",
                "inputSchema": _author_edit_schema(),
                "outputSchema": _object_schema("operation_id", "ok", "diff", "audit"),
            },
            {
                "name": "author_validate",
                "description": "Run authoring validation and optionally scope diagnostics to one source target.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target": _string_schema("Optional source slug or path to scope validation."),
                        "mount": _string_schema("Optional mount id used to disambiguate the target."),
                        "actor": _string_schema("Optional actor id stored in the audit payload."),
                        "privileged_token": _privileged_token_schema(),
                    },
                },
                "outputSchema": _object_schema("ok", "errors", "warnings", "audit"),
            },
            {
                "name": "author_publish",
                "description": "Publish a draft source. Defaults to dry-run and requires confirmed=true to write.",
                "inputSchema": _author_transition_schema(),
                "outputSchema": _object_schema("operation_id", "ok", "audit"),
            },
            {
                "name": "author_unpublish",
                "description": "Move a public source back to draft. Defaults to dry-run and requires confirmed=true to write.",
                "inputSchema": _author_transition_schema(),
                "outputSchema": _object_schema("operation_id", "ok", "audit"),
            },
            {
                "name": "author_archive",
                "description": "Archive a source and remove it from public output. Defaults to dry-run and requires confirmed=true to write.",
                "inputSchema": _author_transition_schema(),
                "outputSchema": _object_schema("operation_id", "ok", "audit"),
            },
            {
                "name": "author_inspect_publication_impact",
                "description": "Inspect lifecycle state, validation diagnostics, and stale impact before publication changes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target": _string_schema("Source slug or path to inspect before publication changes."),
                        "mount": _string_schema("Optional mount id used to disambiguate the target."),
                        "actor": _string_schema("Optional actor id stored in the audit payload."),
                        "privileged_token": _privileged_token_schema(),
                    },
                    "required": ["target"],
                },
                "outputSchema": _object_schema("ok", "status", "validation", "stale_impact", "audit"),
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        arguments = dict(arguments)
        started = time.monotonic()
        self._apply_rate_limit(name, arguments)
        gate = self._policy_gate(name, arguments)
        if gate is not None:
            self._record_tool_audit(
                name,
                arguments,
                status="denied",
                is_error=True,
                payload=gate,
                duration_ms=_duration_ms(started),
            )
            return _tool_result(gate, is_error=True)
        is_error = False
        try:
            if name == "semantic_search":
                payload = self._semantic_search(arguments)
            elif name == "retrieve_node":
                payload = self._retrieve_node(arguments)
            elif name == "query_graph":
                payload = self._query_graph(arguments)
            elif name == "traverse_graph":
                payload = self._traverse_graph(arguments)
            elif name == "inspect_source_health":
                payload = self.source_health(mount=arguments.get("mount"))
            elif name == "run_checks":
                payload = self.validation_report()
            elif name == "explain_stale_impact":
                payload = self.stale_impact_report(slug=arguments.get("slug"))
            elif name == "author_create_draft":
                payload, is_error = self._author_create_draft(arguments)
            elif name == "author_read_source":
                payload, is_error = self._author_read_source(arguments)
            elif name == "author_propose_edit":
                payload, is_error = self._author_apply_edit(arguments, force_dry_run=True)
            elif name == "author_apply_edit":
                payload, is_error = self._author_apply_edit(arguments)
            elif name == "author_validate":
                payload, is_error = self._author_validate(arguments)
            elif name == "author_publish":
                payload, is_error = self._author_transition("publish", arguments)
            elif name == "author_unpublish":
                payload, is_error = self._author_transition("unpublish", arguments)
            elif name == "author_archive":
                payload, is_error = self._author_transition("archive", arguments)
            elif name == "author_inspect_publication_impact":
                payload, is_error = self._author_publication_impact(arguments)
            else:
                raise MCPError(-32602, f"unknown tool: {name}")
            payload = _sanitize_output(payload, max_chars=self.policy.max_output_chars)
            self._record_tool_audit(
                name,
                arguments,
                status="error" if is_error else "ok",
                is_error=is_error,
                payload=payload,
                duration_ms=_duration_ms(started),
            )
            return _tool_result(payload, is_error=is_error)
        except MCPError as exc:
            payload = {
                "schema_version": 1,
                "ok": False,
                "diagnostics": [
                    {
                        "severity": "error",
                        "message": exc.message,
                        "rule_id": "fura.mcp",
                    }
                ],
            }
            self._record_tool_audit(
                name,
                arguments,
                status="error",
                is_error=True,
                payload=payload,
                duration_ms=_duration_ms(started),
            )
            raise

    def source_health(self, *, mount: Any | None = None) -> dict[str, Any]:
        mount_filter = str(mount).strip() if mount else None
        mounts: list[dict[str, Any]] = []
        for item in self.catalog.mounts:
            if mount_filter and item.id != mount_filter:
                continue
            extensions = sorted(item.source.tracked_extensions())
            mounts.append(
                {
                    "id": item.id,
                    "label": item.label,
                    "content_root": str(item.content_root),
                    "exists": item.content_root.is_dir(),
                    "default": item.default,
                    "url_prefix": item.url_prefix,
                    "tracked_extensions": extensions,
                    "file_count": _count_source_files(item.content_root, extensions),
                    "page_count": len([node for node in self._nodes() if node.mount == item.id]),
                    "channels": [_channel_record(ch) for ch in self.catalog.channels_for(item.id)],
                }
            )
        return {
            "schema_version": 1,
            "mount_count": len(mounts),
            "active_channel": self.catalog.active_channel,
            "mounts": mounts,
        }

    def validation_report(self) -> dict[str, Any]:
        errors, warnings = check_catalog(
            self.catalog,
            views=getattr(self.docs_app, "views", None),
            docs=getattr(self.docs_app, "config", None),
            theme=getattr(self.docs_app, "theme", None),
            inventory_store=self.catalog.inventory_store,
        )
        return {
            "schema_version": 1,
            "ok": not errors,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": [{"severity": "error", "message": message} for message in errors],
            "warnings": [{"severity": "warning", "message": message} for message in warnings],
        }

    def stale_impact_report(self, *, slug: Any | None = None) -> dict[str, Any]:
        normalized = str(slug).strip("/") if slug else None
        entries = self.catalog.author_stale_entries(normalized)
        impact = [self._stale_impact_entry(entry) for entry in entries]
        return {
            "schema_version": 1,
            "stale_count": len(entries),
            "entries": entries,
            "impact": impact,
            "groups": {
                "by_owner": _group_impact(impact, "owner"),
                "by_source": _group_impact(impact, "source_key"),
                "by_mount": _group_impact(impact, "mount"),
                "by_tenant": _group_impact(impact, "tenant"),
                "by_site": _group_impact(impact, "site"),
                "by_channel": _group_impact(impact, "output_channel"),
            },
        }

    def _stale_impact_entry(self, entry: dict[str, object]) -> dict[str, Any]:
        slug = str(entry.get("slug") or "")
        mount = str(entry.get("mount") or "")
        node = self.catalog.get_by_slug(slug, mount=mount) if slug and mount else None
        meta = getattr(node, "meta", {}) if node is not None else {}
        owner = _first_meta_value(meta, "owner", "team") or "unassigned"
        provider = _first_meta_value(meta, "source_provider", "provider") or "filesystem"
        repo = _first_meta_value(meta, "source_repo", "repo", "repository")
        ref = _first_meta_value(meta, "source_ref", "ref", "commit", "branch")
        path = getattr(node, "source_path", None) if node is not None else None
        source_key = _source_group_key(provider=provider, repo=repo, ref=ref, path=path)
        output_channel = str(getattr(self.catalog, "active_channel", "") or getattr(node, "edition", "") or "default")
        tenant = _first_meta_value(meta, "tenant") or "default"
        site = _first_meta_value(meta, "site") or "default"
        return {
            "slug": slug,
            "mount": mount,
            "refresh_targets": list(entry.get("hints") or ()),
            "owner": owner,
            "source_key": source_key,
            "tenant": tenant,
            "site": site,
            "output_channel": output_channel,
            "provenance": {
                "provider": provider,
                "repo": repo,
                "ref": ref,
                "path": path,
                "mount": mount,
                "edition": getattr(node, "edition", None) if node is not None else None,
                "owner": owner,
                "tenant": tenant,
                "site": site,
                "output_channel": output_channel,
            },
        }

    def audit_report(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "policy": self.policy.to_dict(),
            "count": len(self.audit_log),
            "entries": list(self.audit_log),
        }

    def _apply_rate_limit(self, name: str, arguments: dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self._rate_window_started >= 60:
            self._rate_window_started = now
            self._rate_count = 0
        self._rate_count += 1
        if self._rate_count <= max(self.policy.rate_limit_per_minute, 1):
            return
        payload = {
            "schema_version": 1,
            "ok": False,
            "diagnostics": [
                {
                    "severity": "error",
                    "message": "MCP tool rate limit exceeded",
                    "rule_id": "fura.mcp.rate_limit",
                    "next_action": "Wait for the current rate-limit window or raise the configured remote MCP limit.",
                }
            ],
            "policy": self.policy.to_dict(),
        }
        self._record_tool_audit(name, arguments, status="rate_limited", is_error=True, payload=payload)
        raise MCPError(-32029, "MCP tool rate limit exceeded", payload)

    def _policy_gate(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        if self.policy.remote and name in _SENSITIVE_TOOLS and not self.policy.has_privileged_token(arguments):
            return {
                "schema_version": 1,
                "ok": False,
                "diagnostics": [
                    {
                        "severity": "error",
                        "message": "remote MCP sensitive tools require a privileged token",
                        "rule_id": "fura.mcp.privileged_token",
                        "next_action": "Pass a valid privileged_token or use local author MCP for source mutations.",
                    }
                ],
                "policy": self.policy.to_dict(),
            }
        return None

    def _record_tool_audit(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        status: str,
        is_error: bool,
        payload: dict[str, Any],
        duration_ms: float | None = None,
    ) -> None:
        self.audit_log.append(
            {
                "timestamp": round(time.time(), 3),
                "actor": _optional_str(arguments.get("actor")) or self.policy.actor,
                "tenant": self.policy.tenant,
                "site": self.policy.site,
                "transport": self.policy.transport,
                "tool": name,
                "inputs": _sanitize_inputs(arguments),
                "status": status,
                "is_error": is_error,
                "result_status": _result_status(payload),
                "timeout_seconds": self.policy.timeout_seconds,
                "duration_ms": duration_ms,
            }
        )

    def _author_create_draft(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        gate = self._author_gate("author_create_draft", arguments)
        if gate is not None:
            return gate, True
        result = author_new(
            str(arguments.get("slug") or ""),
            mounts=self._author_mounts(),
            mount_id=_optional_str(arguments.get("mount")),
            title=_optional_str(arguments.get("title")),
            dry_run=_bool_arg(arguments.get("dry_run"), default=True),
            confirmed=_bool_arg(arguments.get("confirmed"), default=False),
        )
        self._reindex_author_result(result)
        payload = self._author_payload(result, "author_create_draft", arguments)
        return payload, not result.ok

    def _author_read_source(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        gate = self._author_gate("author_read_source", arguments)
        if gate is not None:
            return gate, True
        result, source = author_read_source(
            str(arguments.get("target") or ""),
            mounts=self._author_mounts(),
            mount_id=_optional_str(arguments.get("mount")),
        )
        payload = self._author_payload(result, "author_read_source", arguments)
        payload["source"] = source if result.ok else None
        return payload, not result.ok

    def _author_apply_edit(
        self,
        arguments: dict[str, Any],
        *,
        force_dry_run: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        command = "author_propose_edit" if force_dry_run else "author_apply_edit"
        gate = self._author_gate(command, arguments)
        if gate is not None:
            return gate, True
        dry_run = True if force_dry_run else _bool_arg(arguments.get("dry_run"), default=True)
        result = author_apply_edit(
            str(arguments.get("target") or ""),
            mounts=self._author_mounts(),
            mount_id=_optional_str(arguments.get("mount")),
            old_text=str(arguments.get("old_text") or ""),
            new_text=str(arguments.get("new_text") or ""),
            dry_run=dry_run,
            confirmed=False if force_dry_run else _bool_arg(arguments.get("confirmed"), default=False),
        )
        self._reindex_author_result(result)
        payload = self._author_payload(result, command, arguments)
        return payload, not result.ok

    def _author_transition(self, operation: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        command = f"author_{operation}"
        gate = self._author_gate(command, arguments)
        if gate is not None:
            return gate, True
        result = author_transition(
            operation,
            str(arguments.get("target") or ""),
            mounts=self._author_mounts(),
            mount_id=_optional_str(arguments.get("mount")),
            dry_run=_bool_arg(arguments.get("dry_run"), default=True),
            confirmed=_bool_arg(arguments.get("confirmed"), default=False),
        )
        self._reindex_author_result(result)
        payload = self._author_payload(result, command, arguments)
        return payload, not result.ok

    def _author_validate(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        gate = self._author_gate("author_validate", arguments)
        if gate is not None:
            return gate, True
        report = self.validation_report()
        target = _optional_str(arguments.get("target"))
        if target:
            result = author_validate(
                target,
                mounts=self._author_mounts(),
                mount_id=_optional_str(arguments.get("mount")),
                validation_errors=_validation_messages(report, "errors"),
                validation_warnings=_validation_messages(report, "warnings"),
            )
            payload = self._author_payload(result, "author_validate", arguments)
            _attach_author_validation_fields(payload, result)
            return payload, not result.ok
        report["audit"] = self._audit_record("author_validate", arguments, None)
        return report, not bool(report.get("ok"))

    def _author_publication_impact(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        gate = self._author_gate("author_inspect_publication_impact", arguments)
        if gate is not None:
            return gate, True
        status = author_status(
            str(arguments.get("target") or ""),
            mounts=self._author_mounts(),
            mount_id=_optional_str(arguments.get("mount")),
        )
        status_payload = self._author_payload(status, "author_inspect_publication_impact", arguments)
        if not status.ok:
            return status_payload, True
        report = self.validation_report()
        validation_result = author_validate(
            str(arguments.get("target") or ""),
            mounts=self._author_mounts(),
            mount_id=_optional_str(arguments.get("mount")),
            validation_errors=_validation_messages(report, "errors"),
            validation_warnings=_validation_messages(report, "warnings"),
        )
        validation = self._author_payload(
            validation_result,
            "author_validate",
            arguments,
        )
        _attach_author_validation_fields(validation, validation_result)
        stale = self.stale_impact_report(slug=_optional_str(arguments.get("target")))
        payload = {
            "schema_version": 1,
            "ok": bool(validation_result.ok),
            "status": status_payload,
            "validation": validation,
            "stale_impact": stale,
            "audit": self._audit_record(
                "author_inspect_publication_impact",
                arguments,
                status_payload,
            ),
        }
        return payload, not payload["ok"]

    def _author_mounts(self) -> tuple[Any, ...]:
        config_path = self.docs_app.config.mounts_path or self.docs_app.config.root / "mounts.yaml"
        return load_mounts(config_path, repo_root=self.docs_app.repo_root)

    def _author_gate(self, command: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        if self.include_private:
            return None
        return {
            "schema_version": 1,
            "ok": False,
            "operation": command,
            "diagnostics": [
                {
                    "severity": "error",
                    "message": "authoring tools require an include-private author MCP session",
                    "rule_id": "fura.mcp.author",
                    "next_action": "Start fura mcp --author --include-private for local authoring.",
                }
            ],
            "audit": self._audit_record(command, arguments, None),
        }

    def _author_payload(
        self,
        result: AuthorOperationResult,
        command: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        payload = result.to_dict()
        payload["schema_version"] = 1
        payload["audit"] = self._audit_record(command, arguments, payload)
        return payload

    def _reindex_author_result(self, result: AuthorOperationResult) -> None:
        if not result.ok or not result.changed_files:
            return
        shard = getattr(self.catalog, "_shards", {}).get(result.mount)
        if shard is None:
            self.catalog.refresh_if_stale()
            return
        shard._reindex_paths({Path(path) for path in result.changed_files})
        self.catalog._edges = None
        self.catalog._namespaces = None
        self.catalog._translation_index = None
        self.catalog._finalize_federated()

    def _audit_record(
        self,
        command: str,
        arguments: dict[str, Any],
        result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "actor": _optional_str(arguments.get("actor")) or "mcp-local",
            "command": command,
            "target": _optional_str(arguments.get("target")) or _optional_str(arguments.get("slug")),
            "target_path": result.get("target_path") if result else None,
            "previous_state": result.get("previous_visibility") if result else None,
            "resulting_state": result.get("resulting_visibility") if result else None,
            "diagnostics": result.get("diagnostics", []) if result else [],
            "dry_run": result.get("dry_run") if result else _bool_arg(arguments.get("dry_run"), default=True),
            "confirmed": result.get("confirmed") if result else _bool_arg(arguments.get("confirmed"), default=False),
        }

    def channels_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "active_channel": self.catalog.active_channel,
            "mounts": [
                {
                    "id": mount.id,
                    "label": mount.label,
                    "default": mount.default,
                    "channels": [_channel_record(ch) for ch in self.catalog.channels_for(mount.id)],
                }
                for mount in self.catalog.mounts
            ],
        }

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        client_version = params.get("protocolVersion")
        return {
            "protocolVersion": client_version or MCP_PROTOCOL_VERSION,
            "capabilities": {
                "resources": {},
                "tools": {},
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "version": _package_version(),
            },
        }

    def _resource_payload(self, uri: str) -> dict[str, Any]:
        if uri == "fura://catalog/nodes":
            nodes = [_node_record(node) for node in self._doc_nodes()]
            return {"schema_version": 1, "count": len(nodes), "nodes": nodes}
        if uri == "fura://catalog/graph":
            return catalog_graph(self.catalog, include_private=self.include_private)
        if uri == "fura://catalog/api-operations":
            return self._api_operations()
        if uri == "fura://catalog/structure":
            return build_structure_index(self.catalog)
        if uri == "fura://catalog/inventories":
            return inventories_json(self.catalog, base_url=self.base_url)
        if uri == "fura://catalog/sources":
            return self.source_health()
        if uri == "fura://catalog/channels":
            return self.channels_manifest()
        if uri == "fura://reports/validation":
            return self.validation_report()
        if uri == "fura://reports/stale-impact":
            return self.stale_impact_report()
        if uri == "fura://reports/audit":
            return self.audit_report()
        if uri.startswith("fura://catalog/nodes/"):
            node_id = unquote(uri.removeprefix("fura://catalog/nodes/"))
            node = self._get_by_node_id(node_id)
            if node is None:
                raise MCPError(-32602, f"unknown node resource: {node_id}")
            return self._node_payload(node_id)
        raise MCPError(-32602, f"unknown resource: {uri}")

    def _semantic_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise MCPError(-32602, "semantic_search requires query")
        limit = _bounded_int(arguments.get("limit"), default=12, low=1, high=50)
        result = hybrid_search(
            self.catalog,
            self.embedding_index,
            query,
            limit=limit,
            mount=_optional_str(arguments.get("mount")),
            edition=_optional_str(arguments.get("edition")),
            include_private=self.include_private,
        )
        hits = [
            {
                "node_id": hit.node.node_id,
                "url": _absolute_url(self.base_url, hit.node.url),
                "title": hit.node.title,
                "snippet": hit.snippet,
                "score": hit.score,
                "keyword_score": hit.keyword_score,
                "semantic_score": hit.semantic_score,
                "chunk_id": hit.chunk_id,
                "mount": hit.node.mount,
                "edition": hit.node.edition,
            }
            for hit in result.hits
        ]
        return {
            "schema_version": 1,
            "query": query,
            "mode": "hybrid",
            "count": len(hits),
            "results": hits,
        }

    def _retrieve_node(self, arguments: dict[str, Any]) -> dict[str, Any]:
        node_id = str(arguments.get("node_id") or "").strip()
        if not node_id:
            raise MCPError(-32602, "retrieve_node requires node_id")
        payload = self._node_payload(node_id)
        return payload

    def _query_graph(self, arguments: dict[str, Any]) -> dict[str, Any]:
        include_private = self.include_private and _bool_arg(
            arguments.get("include_private"),
            default=self.include_private,
        )
        return query_catalog_graph(
            self.catalog,
            mount=_optional_str(arguments.get("mount")),
            tag=_optional_str(arguments.get("tag")),
            format=_optional_str(arguments.get("format")),
            owner=_optional_str(arguments.get("owner")) or _optional_str(arguments.get("team")),
            locale=_optional_str(arguments.get("locale")) or _optional_str(arguments.get("lang")),
            edge_kind=(
                _optional_str(arguments.get("edge_kind"))
                or _optional_str(arguments.get("edge"))
                or _optional_str(arguments.get("kind"))
                or _optional_str(arguments.get("link_edge"))
            ),
            source=(
                _optional_str(arguments.get("source"))
                or _optional_str(arguments.get("from"))
                or _optional_str(arguments.get("linked_from"))
            ),
            target=(
                _optional_str(arguments.get("target"))
                or _optional_str(arguments.get("to"))
                or _optional_str(arguments.get("linked_to"))
            ),
            include_private=include_private,
        )

    def _node_payload(self, node_id: str) -> dict[str, Any]:
        if self._get_by_node_id(node_id) is None:
            raise MCPError(-32602, f"unknown node_id: {node_id}")
        payload = retrieve_node(
            self.catalog,
            self.embedding_index,
            node_id,
            include_private=self.include_private,
        )
        if payload is None:
            raise MCPError(-32602, f"unknown node_id: {node_id}")
        return payload

    def _traverse_graph(self, arguments: dict[str, Any]) -> dict[str, Any]:
        node = self._resolve_node(arguments)
        direction = str(arguments.get("direction") or "neighbors")
        limit = _bounded_int(arguments.get("limit"), default=20, low=1, high=100)
        if direction == "backlinks":
            results = self.catalog.backlinks_for(node)[:limit]
        elif direction == "children":
            results = self._child_records(node, limit=limit)
        elif direction == "outbound":
            results = self._outbound_records(node, limit=limit)
        elif direction == "neighbors":
            prev_node, next_node = self.catalog.prev_next(node)
            related = [
                {"relation": "parent", **item} for item in self.catalog.trail(node)[-2:-1]
            ]
            if prev_node is not None:
                related.append({"relation": "previous", **_node_ref(prev_node)})
            if next_node is not None:
                related.append({"relation": "next", **_node_ref(next_node)})
            related.extend({"relation": "backlink", **item} for item in self.catalog.backlinks_for(node))
            related.extend({"relation": "child", **item} for item in self._child_records(node, limit=limit))
            results = related[:limit]
        else:
            raise MCPError(-32602, f"unsupported direction: {direction}")
        return {
            "schema_version": 1,
            "node": _node_ref(node),
            "direction": direction,
            "count": len(results),
            "results": results,
        }

    def _resolve_node(self, arguments: dict[str, Any]):
        node_id = str(arguments.get("node_id") or "").strip()
        if node_id:
            node = self._get_by_node_id(node_id)
            if node is None:
                raise MCPError(-32602, f"unknown node_id: {node_id}")
            return node
        url = str(arguments.get("url") or "").strip()
        if url:
            node = self.catalog.get_path(url)
            if node is None:
                raise MCPError(-32602, f"unknown url: {url}")
            return node
        raise MCPError(-32602, "traverse_graph requires node_id or url")

    def _get_by_node_id(self, node_id: str):
        try:
            node = self.catalog.get_by_node_id(node_id)
        except ValueError:
            return None
        if node is None:
            return None
        if not self.include_private and node not in public_nodes([node]):
            return None
        return node

    def _child_records(self, node: Any, *, limit: int) -> list[dict[str, Any]]:
        prefix = f"{node.slug.strip('/')}/" if node.slug.strip("/") else ""
        children = []
        candidates = self.catalog.doc_nodes(lang=node.lang)
        if not self.include_private:
            candidates = public_nodes(candidates)
        for candidate in candidates:
            if candidate.node_id == node.node_id or candidate.mount != node.mount:
                continue
            candidate_slug = candidate.slug.strip("/")
            if prefix and not candidate_slug.startswith(prefix):
                continue
            remainder = candidate_slug[len(prefix):] if prefix else candidate_slug
            if "/" in remainder.strip("/"):
                continue
            children.append(_node_ref(candidate))
        return children[:limit]

    def _outbound_records(self, node: Any, *, limit: int) -> list[dict[str, Any]]:
        content_ir = node.content_ir
        if content_ir is None:
            return []
        records = []
        for link in content_ir.links:
            records.append(
                {
                    "href": link.href,
                    "text": link.text,
                    "line": link.line,
                    "resolved": link.resolved,
                    "mount": link.mount,
                    "domain": link.domain,
                    "inventory_id": link.inventory_id,
                }
            )
        return records[:limit]

    def _api_operations(self) -> dict[str, Any]:
        operations = []
        for node in self._doc_nodes():
            if "api" not in node.tags and node.meta.get("source") != "autodoc":
                continue
            record = {
                "node_id": node.node_id,
                "url": node.url,
                "title": node.title,
                "description": node.description,
                "mount": node.mount,
                "edition": node.edition,
                "source_path": node.source_path,
                "element_type": node.meta.get("element_type"),
                "qualified_name": node.meta.get("qualified_name"),
                "tags": sorted(node.tags),
            }
            api_operation = node.meta.get("api_operation")
            if isinstance(api_operation, dict):
                record.update(
                    {
                        "operation_id": api_operation.get("operation_id"),
                        "method": api_operation.get("method"),
                        "path": api_operation.get("path"),
                        "summary": api_operation.get("summary"),
                        "schemas": api_operation.get("schemas") or [],
                        "request_bodies": api_operation.get("request_bodies") or [],
                        "responses": api_operation.get("responses") or [],
                        "examples": api_operation.get("examples") or [],
                        "auth": api_operation.get("auth") or [],
                        "environments": api_operation.get("environments") or [],
                    }
                )
            operations.append(record)
        return {"schema_version": 1, "count": len(operations), "operations": operations}

    def _nodes(self) -> list[Any]:
        nodes = list(self.catalog.nodes)
        return nodes if self.include_private else public_nodes(nodes)

    def _doc_nodes(self) -> list[Any]:
        nodes = self.catalog.doc_nodes()
        return nodes if self.include_private else public_nodes(nodes)

    @staticmethod
    def _response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error_response(
        request_id: Any,
        code: int,
        message: str,
        data: Any | None = None,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}


def build_milo_cli(server: FuraMCPServer):
    """Build a Milo CLI exposing this catalog as MCP tools/resources."""
    from milo.commands import CLI

    cli = CLI(
        name=SERVER_NAME,
        description="Furatena catalog MCP control plane.",
        version=_package_version(),
    )

    for resource in server.list_resources():
        _register_milo_resource(cli, server, resource)

    @cli.command(
        "semantic_search",
        description="Hybrid keyword + semantic search over catalog pages and chunks.",
        annotations={"readOnlyHint": True},
    )
    def semantic_search(
        query: str,
        limit: int = 12,
        mount: str = "",
        edition: str = "",
    ) -> dict:
        return _milo_tool_payload(
            server,
            "semantic_search",
            {"query": query, "limit": limit, "mount": mount, "edition": edition},
        )

    @cli.command(
        "retrieve_node",
        description="Retrieve a catalog node with chunks, backlinks, and similar pages.",
        annotations={"readOnlyHint": True},
    )
    def retrieve_node_tool(node_id: str) -> dict:
        return _milo_tool_payload(server, "retrieve_node", {"node_id": node_id})

    @cli.command(
        "query_graph",
        description="Filter the DCP catalog graph by page metadata and edge source, target, or kind.",
        annotations={"readOnlyHint": True},
    )
    def query_graph(
        mount: str = "",
        tag: str = "",
        format: str = "",
        owner: str = "",
        team: str = "",
        locale: str = "",
        lang: str = "",
        edge_kind: str = "",
        edge: str = "",
        kind: str = "",
        link_edge: str = "",
        source: str = "",
        linked_from: str = "",
        target: str = "",
        linked_to: str = "",
        include_private: bool = False,
    ) -> dict:
        return _milo_tool_payload(
            server,
            "query_graph",
            {
                "mount": mount,
                "tag": tag,
                "format": format,
                "owner": owner,
                "team": team,
                "locale": locale,
                "lang": lang,
                "edge_kind": edge_kind,
                "edge": edge,
                "kind": kind,
                "link_edge": link_edge,
                "source": source,
                "linked_from": linked_from,
                "target": target,
                "linked_to": linked_to,
                "include_private": include_private,
            },
        )

    @cli.command(
        "traverse_graph",
        description="Traverse backlinks, child pages, outbound links, or neighboring pages.",
        annotations={"readOnlyHint": True},
    )
    def traverse_graph(
        node_id: str = "",
        url: str = "",
        direction: str = "neighbors",
        limit: int = 20,
    ) -> dict:
        return _milo_tool_payload(
            server,
            "traverse_graph",
            {"node_id": node_id, "url": url, "direction": direction, "limit": limit},
        )

    @cli.command(
        "inspect_source_health",
        description="Inspect source roots, mount health, and channel coverage.",
        annotations={"readOnlyHint": True},
    )
    def inspect_source_health(mount: str = "") -> dict:
        return _milo_tool_payload(server, "inspect_source_health", {"mount": mount})

    @cli.command(
        "run_checks",
        description="Run Furatena content, link, schema, theme, and view checks.",
        annotations={"readOnlyHint": True},
    )
    def run_checks() -> dict:
        return _milo_tool_payload(server, "run_checks", {})

    @cli.command(
        "explain_stale_impact",
        description="Explain author-mode stale entries and impacted refresh targets.",
        annotations={"readOnlyHint": True},
    )
    def explain_stale_impact(slug: str = "") -> dict:
        return _milo_tool_payload(server, "explain_stale_impact", {"slug": slug})

    @cli.command(
        "author_create_draft",
        description="Create a draft source file. Defaults to dry-run and requires confirmed=true to write.",
        annotations={"destructiveHint": True},
    )
    def author_create_draft(
        slug: str,
        title: str = "",
        mount: str = "",
        dry_run: bool = True,
        confirmed: bool = False,
        actor: str = "",
        privileged_token: str = "",
    ) -> dict:
        return _milo_tool_payload(
            server,
            "author_create_draft",
            {
                "slug": slug,
                "title": title,
                "mount": mount,
                "dry_run": dry_run,
                "confirmed": confirmed,
                "actor": actor,
                "privileged_token": privileged_token,
            },
        )

    @cli.command(
        "author_read_source",
        description="Read a source file for authoring. Requires an include-private author MCP session.",
        annotations={"readOnlyHint": True},
    )
    def author_read_source_tool(
        target: str,
        mount: str = "",
        actor: str = "",
        privileged_token: str = "",
    ) -> dict:
        return _milo_tool_payload(
            server,
            "author_read_source",
            {"target": target, "mount": mount, "actor": actor, "privileged_token": privileged_token},
        )

    @cli.command(
        "author_propose_edit",
        description="Preview an exact-text source edit without writing files.",
        annotations={"readOnlyHint": True},
    )
    def author_propose_edit(
        target: str,
        old_text: str,
        new_text: str,
        mount: str = "",
        dry_run: bool = True,
        confirmed: bool = False,
        actor: str = "",
        privileged_token: str = "",
    ) -> dict:
        return _milo_tool_payload(
            server,
            "author_propose_edit",
            {
                "target": target,
                "old_text": old_text,
                "new_text": new_text,
                "mount": mount,
                "dry_run": dry_run,
                "confirmed": confirmed,
                "actor": actor,
                "privileged_token": privileged_token,
            },
        )

    @cli.command(
        "author_apply_edit",
        description="Apply an exact-text source edit. Defaults to dry-run and requires confirmed=true to write.",
        annotations={"destructiveHint": True},
    )
    def author_apply_edit_tool(
        target: str,
        old_text: str,
        new_text: str,
        mount: str = "",
        dry_run: bool = True,
        confirmed: bool = False,
        actor: str = "",
        privileged_token: str = "",
    ) -> dict:
        return _milo_tool_payload(
            server,
            "author_apply_edit",
            {
                "target": target,
                "old_text": old_text,
                "new_text": new_text,
                "mount": mount,
                "dry_run": dry_run,
                "confirmed": confirmed,
                "actor": actor,
                "privileged_token": privileged_token,
            },
        )

    @cli.command(
        "author_validate",
        description="Run authoring validation and optionally scope diagnostics to one source target.",
        annotations={"readOnlyHint": True},
    )
    def author_validate(
        target: str = "",
        mount: str = "",
        actor: str = "",
        privileged_token: str = "",
    ) -> dict:
        return _milo_tool_payload(
            server,
            "author_validate",
            {"target": target, "mount": mount, "actor": actor, "privileged_token": privileged_token},
        )

    @cli.command(
        "author_publish",
        description="Publish a draft source. Defaults to dry-run and requires confirmed=true to write.",
        annotations={"destructiveHint": True},
    )
    def author_publish(
        target: str,
        mount: str = "",
        dry_run: bool = True,
        confirmed: bool = False,
        actor: str = "",
        privileged_token: str = "",
    ) -> dict:
        return _milo_tool_payload(
            server,
            "author_publish",
            {
                "target": target,
                "mount": mount,
                "dry_run": dry_run,
                "confirmed": confirmed,
                "actor": actor,
                "privileged_token": privileged_token,
            },
        )

    @cli.command(
        "author_unpublish",
        description="Move a public source back to draft. Defaults to dry-run and requires confirmed=true to write.",
        annotations={"destructiveHint": True},
    )
    def author_unpublish(
        target: str,
        mount: str = "",
        dry_run: bool = True,
        confirmed: bool = False,
        actor: str = "",
        privileged_token: str = "",
    ) -> dict:
        return _milo_tool_payload(
            server,
            "author_unpublish",
            {
                "target": target,
                "mount": mount,
                "dry_run": dry_run,
                "confirmed": confirmed,
                "actor": actor,
                "privileged_token": privileged_token,
            },
        )

    @cli.command(
        "author_archive",
        description="Archive a source and remove it from public output. Defaults to dry-run and requires confirmed=true to write.",
        annotations={"destructiveHint": True},
    )
    def author_archive(
        target: str,
        mount: str = "",
        dry_run: bool = True,
        confirmed: bool = False,
        actor: str = "",
        privileged_token: str = "",
    ) -> dict:
        return _milo_tool_payload(
            server,
            "author_archive",
            {
                "target": target,
                "mount": mount,
                "dry_run": dry_run,
                "confirmed": confirmed,
                "actor": actor,
                "privileged_token": privileged_token,
            },
        )

    @cli.command(
        "author_inspect_publication_impact",
        description="Inspect lifecycle state, validation diagnostics, and stale impact before publication changes.",
        annotations={"readOnlyHint": True},
    )
    def author_inspect_publication_impact(
        target: str,
        mount: str = "",
        actor: str = "",
        privileged_token: str = "",
    ) -> dict:
        return _milo_tool_payload(
            server,
            "author_inspect_publication_impact",
            {"target": target, "mount": mount, "actor": actor, "privileged_token": privileged_token},
        )

    return cli


def run_milo_stdio(server: FuraMCPServer) -> None:
    """Run the Furatena MCP surface through Milo's stdio transport."""
    from milo.mcp import run_mcp_server

    run_mcp_server(build_milo_cli(server))


def run_stdio(server: FuraMCPServer) -> None:
    """Run a newline-delimited JSON-RPC MCP loop on stdio."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = FuraMCPServer._error_response(None, -32700, str(exc))
        else:
            response = server.handle_request(request)
        if response is not None:
            sys.stdout.write(_dumps(response) + "\n")
            sys.stdout.flush()


def _register_milo_resource(cli: Any, server: FuraMCPServer, resource: dict[str, Any]) -> None:
    uri = str(resource["uri"])

    @cli.resource(
        uri,
        name=str(resource.get("name") or uri),
        description=str(resource.get("description") or ""),
        mime_type=str(resource.get("mimeType") or "application/json"),
    )
    def read_resource(_uri: str = uri) -> dict:
        return server._resource_payload(_uri)


def _milo_tool_payload(
    server: FuraMCPServer,
    name: str,
    arguments: dict[str, Any],
) -> dict:
    result = server.call_tool(name, arguments)
    payload = result.get("structuredContent")
    if result.get("isError"):
        if isinstance(payload, dict):
            return payload
        raise MCPError(-32602, _dumps(result))
    return payload if isinstance(payload, dict) else {"result": payload}


def _resource(uri: str, name: str, description: str) -> dict[str, str]:
    return {
        "uri": uri,
        "name": name,
        "description": description,
        "mimeType": "application/json",
    }


def _node_uri(node_id: str) -> str:
    return f"fura://catalog/nodes/{quote(node_id, safe='')}"


def _node_record(node: Any) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "url": node.url,
        "slug": node.slug,
        "title": node.title,
        "description": node.description,
        "mount": node.mount,
        "edition": node.edition,
        "section": node.section,
        "tags": sorted(node.tags),
        "source_path": node.source_path,
        "lang": node.lang,
        "layout": node.layout,
    }


def _node_ref(node: Any) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "url": node.url,
        "title": node.title,
        "mount": node.mount,
        "edition": node.edition,
    }


def _channel_record(channel: Any) -> dict[str, Any]:
    return {
        "id": channel.id,
        "label": channel.label,
        "default": channel.default,
    }


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    structured = _jsonable(payload)
    return {
        "content": [
            {
                "type": "text",
                "text": _dumps(structured),
            }
        ],
        "structuredContent": structured,
        "isError": is_error,
    }


def _object_schema(*required: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": True,
        "required": list(required),
        "properties": {key: {"description": f"Stable result field: {key}."} for key in required},
    }


def _author_edit_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "target": _string_schema("Existing source slug or path to edit."),
            "old_text": _string_schema("Exact source text that must be replaced."),
            "new_text": _string_schema("Replacement source text."),
            "mount": _string_schema("Optional mount id used to disambiguate the target."),
            "dry_run": _boolean_schema("When true, preview the edit without changing files.", default=True),
            "confirmed": _boolean_schema(
                "Must be true with dry_run=false before files are written.",
                default=False,
            ),
            "actor": _string_schema("Optional actor id stored in the audit payload."),
            "privileged_token": _privileged_token_schema(),
        },
        "required": ["target", "old_text", "new_text"],
    }


def _author_transition_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "target": _string_schema("Existing source slug or path whose lifecycle state changes."),
            "mount": _string_schema("Optional mount id used to disambiguate the target."),
            "dry_run": _boolean_schema("When true, preview the transition without changing files.", default=True),
            "confirmed": _boolean_schema(
                "Must be true with dry_run=false before files are written.",
                default=False,
            ),
            "actor": _string_schema("Optional actor id stored in the audit payload."),
            "privileged_token": _privileged_token_schema(),
        },
        "required": ["target"],
    }


def _string_schema(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "description": description, **extra}


def _integer_schema(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "integer", "description": description, **extra}


def _boolean_schema(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "boolean", "description": description, **extra}


def _privileged_token_schema() -> dict[str, Any]:
    return _string_schema("Remote MCP privileged token for sensitive authoring tools.")


def _filter_validation_report(report: dict[str, Any], source_path: str | None) -> dict[str, Any]:
    if not source_path:
        return report
    filtered = dict(report)
    errors = [
        item
        for item in report.get("errors", [])
        if not item.get("source_path") or item.get("source_path") == source_path
    ]
    warnings = [
        item
        for item in report.get("warnings", [])
        if not item.get("source_path") or item.get("source_path") == source_path
    ]
    filtered["errors"] = errors
    filtered["warnings"] = warnings
    filtered["error_count"] = len(errors)
    filtered["warning_count"] = len(warnings)
    filtered["ok"] = not errors
    filtered["target_path"] = source_path
    return filtered


def _validation_messages(report: dict[str, Any], key: str) -> tuple[str, ...]:
    return tuple(
        str(item.get("message") or "")
        for item in report.get(key, [])
        if isinstance(item, dict) and item.get("message")
    )


def _attach_author_validation_fields(payload: dict[str, Any], result: AuthorOperationResult) -> None:
    errors = [
        {
            "severity": diagnostic.severity,
            "message": diagnostic.message,
            "source_path": diagnostic.source_path,
            "rule_id": diagnostic.rule_id,
            "next_action": diagnostic.next_action,
        }
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]
    warnings = [
        {
            "severity": diagnostic.severity,
            "message": diagnostic.message,
            "source_path": diagnostic.source_path,
            "rule_id": diagnostic.rule_id,
            "next_action": diagnostic.next_action,
        }
        for diagnostic in result.diagnostics
        if diagnostic.severity == "warning"
    ]
    payload["errors"] = errors
    payload["warnings"] = warnings
    payload["error_count"] = len(errors)
    payload["warning_count"] = len(warnings)


def _first_meta_value(meta: Any, *keys: str) -> str | None:
    if not isinstance(meta, dict):
        return None
    for key in keys:
        value = meta.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _source_group_key(
    *,
    provider: str,
    repo: str | None,
    ref: str | None,
    path: Any,
) -> str:
    parts = [provider]
    if repo:
        parts.append(repo)
    if ref:
        parts[-1] = f"{parts[-1]}@{ref}"
    if path:
        parts.append(str(path))
    return ":".join(parts)


def _group_impact(impact: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in impact:
        key = str(entry.get(field) or "unknown")
        groups.setdefault(key, []).append(entry)
    return [
        {
            "key": key,
            "count": len(items),
            "slugs": sorted(str(item.get("slug") or "") for item in items),
            "refresh_targets": sorted(
                {
                    str(target)
                    for item in items
                    for target in (item.get("refresh_targets") or [])
                }
            ),
        }
        for key, items in sorted(groups.items())
    ]


def _bool_arg(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _count_source_files(root: Path, extensions: list[str]) -> int:
    if not root.is_dir():
        return 0
    normalized = tuple(ext if ext.startswith(".") else f".{ext}" for ext in extensions)
    return sum(
        1
        for path in root.rglob("*")
        if path.is_file() and (not normalized or path.suffix in normalized)
    )


def _bounded_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _sanitize_inputs(value: Any) -> Any:
    if is_dataclass(value):
        return _sanitize_inputs(asdict(value))
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if text_key.lower() in _TOKEN_KEYS:
                sanitized[text_key] = "<redacted>"
            else:
                sanitized[text_key] = _sanitize_inputs(item)
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize_inputs(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _sanitize_output(value: Any, *, max_chars: int) -> dict[str, Any]:
    jsonable = _sanitize_inputs(value)
    limit = max(max_chars, 1)
    rendered = _dumps(jsonable)
    if len(rendered) <= limit:
        return jsonable if isinstance(jsonable, dict) else {"value": jsonable}
    return {
        "schema_version": 1,
        "ok": _result_status(jsonable) != "error",
        "truncated": True,
        "max_output_chars": limit,
        "content": f"{rendered[:limit]}...[truncated]",
    }


def _result_status(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "ok"
    if payload.get("ok") is False:
        return "error"
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, list) and any(
        isinstance(item, dict) and item.get("severity") == "error" for item in diagnostics
    ):
        return "error"
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        return "error"
    return "ok"


def _duration_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 3)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _absolute_url(base_url: str, path: str) -> str:
    if not base_url:
        return path
    return f"{base_url.rstrip('/')}{path}"


def _dumps(payload: Any) -> str:
    return json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _package_version() -> str:
    try:
        import furatena

        return str(furatena.__version__)
    except Exception:  # pragma: no cover - defensive metadata lookup
        return "0"
