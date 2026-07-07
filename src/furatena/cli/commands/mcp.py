"""mcp command parser and execution."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from furatena.cli.commands._shared import (
    CommandModule,
    _app_root,
    _autodoc_config,
    _docs_yaml,
    _ensure_pythonpath,
    _finish_result,
    _json_output,
    _repo_for_app,
)
from furatena.cli.contracts import CommandResult, command_name


def _run_mcp(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    sys.path.insert(0, str(_app_root(args)))
    from furatena.catalog.access import AccessRole
    from furatena.catalog.audit_store import InMemoryAuditStore, JsonLinesAuditStore
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.mcp import FuraMCPServer, MCPAccessPolicy, run_milo_stdio
    from furatena.catalog.rate_limit import (
        InMemoryRateLimitStore,
        ResilientRateLimitStore,
    )
    from furatena.catalog.runtime import ServeConfig, ServeMode

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    docs_yaml = _docs_yaml(args)
    if not docs_yaml.is_file():
        raise SystemExit(f"docs config not found: {docs_yaml}")

    frozen_dir = (
        Path(args.frozen_dir).expanduser().resolve() if args.frozen_dir else app_root / "frozen"
    )
    if args.preview:
        serve = ServeConfig(ServeMode.PREVIEW, frozen_dir, True, False)
    elif args.hybrid:
        serve = ServeConfig(ServeMode.HYBRID, frozen_dir, True, False)
    else:
        serve = ServeConfig(ServeMode.AUTHOR, None, False, False)
    docs = DocsApp.from_paths(
        docs_yaml,
        repo_root=repo_root,
        autodoc_config=_autodoc_config(args, repo_root),
        autodoc=not args.no_autodoc,
        serve=serve,
        workers=args.workers,
    )
    privileged_tokens = frozenset(token for token in [args.privileged_token] if token)
    allow_private = serve.mode == ServeMode.AUTHOR and args.include_private
    if args.remote and not privileged_tokens:
        allow_private = False
    policy = MCPAccessPolicy(
        transport="remote" if args.remote else "local",
        actor=args.actor or ("mcp-remote" if args.remote else "mcp-local"),
        tenant=args.tenant,
        site=args.site,
        allow_private=allow_private,
        roles=frozenset(AccessRole(role) for role in args.role),
        teams=frozenset(args.team),
        privileged_tokens=privileged_tokens,
        rate_limit_per_minute=args.rate_limit,
        tenant_rate_limit_per_minute=args.tenant_rate_limit,
        rate_limit_burst=args.rate_limit_burst,
        sensitive_rate_limit_per_minute=args.sensitive_rate_limit,
        timeout_seconds=args.timeout,
        max_output_chars=args.max_output_chars,
    )
    audit_store = (
        JsonLinesAuditStore(
            Path(args.audit_store),
            retention_days=args.audit_retention_days,
        )
        if args.audit_store
        else InMemoryAuditStore(retention_days=args.audit_retention_days)
    )
    rate_limit_store = (
        ResilientRateLimitStore.from_sqlite(
            Path(args.rate_limit_store),
            fallback_mode=args.rate_limit_fallback,
        )
        if args.rate_limit_store
        else InMemoryRateLimitStore()
    )
    server = FuraMCPServer(
        docs,
        base_url=args.base_url or "",
        include_private=serve.mode == ServeMode.AUTHOR and args.include_private,
        policy=policy,
        audit_store=audit_store,
        rate_limit_store=rate_limit_store,
    )

    if args.describe:
        data = {
            "protocol_version": "2025-06-18",
            "transport": "milo-stdio",
            "policy": server.policy.to_dict(),
            "audit": server.audit_store.export(),
            "rate_limit": server.rate_limit_store.describe(),
            "resources": server.list_resources(),
            "tools": server.list_tools(),
        }
        if _json_output(args):
            _finish_result(
                CommandResult(
                    command=command_name(args),
                    ok=True,
                    summary=(
                        f"MCP server exposes {len(data['resources'])} resource(s) "
                        f"and {len(data['tools'])} tool(s)"
                    ),
                    data=data,
                ),
                json_output=True,
            )
            return
        print(f"MCP protocol: {data['protocol_version']}")
        print("Resources:")
        for resource in data["resources"]:
            print(f"  {resource['uri']}\t{resource['name']}")
        print("Tools:")
        for tool in data["tools"]:
            print(f"  {tool['name']}\t{tool['description']}")
        return

    run_milo_stdio(server)


def configure(sub: Any) -> None:
    mcp = sub.add_parser("mcp", help="Run a local MCP server over stdio")
    mcp_mode = mcp.add_mutually_exclusive_group()
    mcp_mode.add_argument("--author", action="store_true", help="Serve live source catalog data")
    mcp_mode.add_argument("--preview", action="store_true", help="Serve frozen catalog data")
    mcp_mode.add_argument(
        "--hybrid", action="store_true", help="Serve frozen baseline with live overlay"
    )
    mcp.add_argument(
        "--frozen-dir", default=None, help="Frozen catalog directory (default app/frozen)"
    )
    mcp.add_argument("--no-autodoc", action="store_true", help="Skip autodoc slice")
    mcp.add_argument("--base-url", default="", help="Public origin for absolute search URLs")
    mcp.add_argument("--workers", type=int, default=None, help="Parallel index workers")
    mcp.add_argument(
        "--include-private",
        action="store_true",
        help="In author mode, expose draft/private nodes through MCP resources and tools",
    )
    mcp.add_argument(
        "--remote", action="store_true", help="Apply remote MCP auth, audit, and safety policy"
    )
    mcp.add_argument("--actor", default="", help="Actor id recorded in MCP audit events")
    mcp.add_argument(
        "--role",
        action="append",
        default=[],
        choices=("anonymous", "reader", "contributor", "publisher", "admin"),
        help="Trusted MCP session role; may be repeated (remote defaults to anonymous)",
    )
    mcp.add_argument(
        "--team",
        action="append",
        default=[],
        help="Trusted MCP session team; may be repeated",
    )
    mcp.add_argument("--tenant", default=None, help="Tenant id recorded in MCP audit events")
    mcp.add_argument("--site", default=None, help="Site id recorded in MCP audit events")
    mcp.add_argument(
        "--audit-store",
        default=None,
        help="Persist sanitized MCP audit events to this JSONL path",
    )
    mcp.add_argument(
        "--audit-retention-days",
        type=int,
        default=90,
        help="Retain MCP audit events for this many days (default 90)",
    )
    mcp.add_argument(
        "--privileged-token",
        default="",
        help="Token required by remote MCP clients before sensitive authoring tools can run",
    )
    mcp.add_argument(
        "--rate-limit",
        type=int,
        default=120,
        help="Maximum MCP tool calls per actor per minute",
    )
    mcp.add_argument(
        "--tenant-rate-limit",
        type=int,
        default=600,
        help="Maximum MCP tool calls per tenant per minute across actors",
    )
    mcp.add_argument(
        "--rate-limit-burst",
        type=int,
        default=20,
        help="Maximum MCP tool calls per actor in a one-second burst",
    )
    mcp.add_argument(
        "--sensitive-rate-limit",
        type=int,
        default=30,
        help="Maximum sensitive MCP tool calls per actor per minute",
    )
    mcp.add_argument(
        "--rate-limit-store",
        default=None,
        help="Share restart-safe MCP rate limits through this SQLite path",
    )
    mcp.add_argument(
        "--rate-limit-fallback",
        choices=("deny", "memory"),
        default="deny",
        help="Behavior when a configured shared rate-limit store is unavailable",
    )
    mcp.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Declared MCP tool timeout in seconds for audit and client policy metadata",
    )
    mcp.add_argument(
        "--max-output-chars",
        type=int,
        default=200_000,
        help="Maximum serialized characters returned by one MCP tool before truncation",
    )
    mcp.add_argument(
        "--describe", action="store_true", help="Describe MCP resources/tools and exit"
    )
    mcp.add_argument(
        "--json", action="store_true", help="With --describe, emit the standard command result JSON"
    )
    mcp.set_defaults(handler=_run_mcp)


COMMAND = CommandModule("mcp", configure)
