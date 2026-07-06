"""check command parser and execution."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from furatena.cli.commands._shared import (
    CommandModule,
    _app_root,
    _autodoc_config,
    _docs_yaml,
    _ensure_pythonpath,
    _repo_for_app,
)
from furatena.cli.contracts import (
    CommandResult,
    Diagnostic,
    ExitCode,
    command_name,
    diagnostic_from_message,
)


def _run_docs_content_check(
    *,
    args: argparse.Namespace,
    warnings_as_errors: bool = False,
    strict_views: bool = False,
    strict_edition_links: bool = False,
) -> tuple[list[str], list[str]]:
    from furatena.catalog.check import check_catalog
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.lifecycle import check_stale_public_outputs
    from furatena.catalog.registry import CatalogRegistry
    from furatena.catalog.theme import DocsTheme
    from furatena.catalog.views import ViewRegistry

    app_root = _app_root(args)
    repo = _repo_for_app(app_root)
    config = load_docs_config(_docs_yaml(args))
    theme = DocsTheme.from_docs_config(config)
    mounts_path = config.mounts_path or app_root / "mounts.yaml"
    autodoc_config = _autodoc_config(args, repo)
    registry = CatalogRegistry.from_config(
        mounts_path,
        repo_root=repo,
        app_root=app_root,
        rewrites_path=config.rewrites_path,
        inventories_path=config.inventories_path,
        catalog_identity=config.identity.to_meta(),
        autodoc=False,
        autodoc_config=autodoc_config,
    )
    views = ViewRegistry(config)
    errors, warnings = check_catalog(
        registry,
        views=views,
        docs=config,
        theme=theme,
        strict_views=strict_views,
        strict_edition_links=strict_edition_links,
        inventory_store=registry.inventory_store,
    )
    from furatena.catalog.api_governance import lint_openapi_autodoc_config

    api_errors, api_warnings = lint_openapi_autodoc_config(
        autodoc_config,
        repo_root=repo,
    )
    errors.extend(api_errors)
    warnings.extend(api_warnings)
    stale_public_outputs = check_stale_public_outputs(registry, app_root / "frozen")
    if stale_public_outputs and getattr(args, "deploy", False):
        errors.extend(stale_public_outputs)
    else:
        warnings.extend(stale_public_outputs)
    return errors, warnings


def _content_rule_id(message: str, *, dcp_errors: list[str]) -> str:
    if message in dcp_errors:
        return "fura.dcp"
    if (
        "OpenAPI" in message
        or "operationId" in message
        or "schema reference" in message
        or "broken example" in message
    ):
        return "fura.api"
    lifecycle_markers = (
        "visibility must be one of",
        "draft pages cannot",
        "pages cannot set published_at",
        "archived pages cannot",
        "public page links to draft/private target",
        "public lifecycle pages should set published_at",
        "public output is stale",
        "owner must not be empty",
        "reviewers must be",
    )
    if any(marker in message for marker in lifecycle_markers):
        return "fura.lifecycle"
    return "fura.content"


def _run_chirp_app_check(args: argparse.Namespace):
    # Freeze without Chirp's debug-time terminal renderer; the explicit
    # structured check below is the single source for every output mode.
    skip_env = "CHIRP_SKIP_CONTRACT_CHECKS"
    previous_skip = os.environ.get(skip_env)
    os.environ[skip_env] = "1"
    try:
        if args.app:
            from chirp.cli._resolve import resolve_app

            sys.path.insert(0, str(_app_root(args)))
            app = resolve_app(args.app)
        else:
            from furatena.catalog.docs_app import DocsApp
            from furatena.catalog.runtime import ServeConfig, ServeMode

            app_root = _app_root(args)
            repo_root = _repo_for_app(app_root)
            docs = DocsApp.from_paths(
                _docs_yaml(args),
                repo_root=repo_root,
                autodoc_config=_autodoc_config(args, repo_root),
                autodoc=False,
                serve=ServeConfig(ServeMode.AUTHOR, None, False, True),
            )
            app = docs.app
        app.freeze()
    finally:
        if previous_skip is None:
            os.environ.pop(skip_env, None)
        else:
            os.environ[skip_env] = previous_skip
    from chirp.contracts import check_hypermedia_surface

    return check_hypermedia_surface(app, deploy=args.deploy)


def _chirp_diagnostic(issue) -> Diagnostic:
    category = str(issue.category or "contract")
    return Diagnostic(
        severity=issue.severity.value,
        message=issue.message,
        source_path=issue.template or issue.route,
        rule_id=f"chirp.{category}",
        next_action=(
            issue.details
            or f"Fix the Chirp {category.replace('_', ' ')} contract and rerun fura check."
        ),
    )


def _run_dcp_file_checks(args: argparse.Namespace) -> tuple[list[str], int]:
    from furatena.catalog.dcp_validate import dcp_fixture_paths, validate_catalog_json_file

    paths: list[Path] = []
    for raw in getattr(args, "dcp_file", None) or ():
        paths.append(Path(raw).expanduser().resolve())
    if getattr(args, "dcp_fixtures", False):
        paths.extend(dcp_fixture_paths())
    errors: list[str] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            errors.append(f"{path}: DCP catalog fixture not found")
            continue
        try:
            errors.extend(validate_catalog_json_file(path))
        except Exception as exc:
            errors.append(f"{path}: failed to validate DCP catalog: {exc}")
    return sorted(errors), len(seen)


def _run_agent_checks(
    args: argparse.Namespace,
    *,
    include_safety: bool = False,
    stale_public_outputs: tuple[str, ...] = (),
):
    from furatena.catalog.agent_lint import check_agent_contracts, check_agent_safety
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.impact import stale_impact_report
    from furatena.catalog.mcp import FuraMCPServer
    from furatena.catalog.runtime import ServeConfig, ServeMode

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    docs = DocsApp.from_paths(
        _docs_yaml(args),
        repo_root=repo_root,
        autodoc_config=_autodoc_config(args, repo_root),
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    server = FuraMCPServer(docs, include_private=False)
    errors, warnings = check_agent_contracts(server)
    if include_safety:
        stale_report = stale_impact_report(
            docs.catalog,
            stale_public_outputs=tuple(dict.fromkeys(stale_public_outputs)),
            include_private=False,
        )
        safety_errors, safety_warnings = check_agent_safety(server, stale_report=stale_report)
        errors.extend(safety_errors)
        warnings.extend(safety_warnings)
    return sorted(
        errors, key=lambda finding: (finding.rule_id, finding.target, finding.message)
    ), sorted(
        warnings,
        key=lambda finding: (finding.rule_id, finding.target, finding.message),
    )


def _agent_diagnostic(finding) -> Diagnostic:
    return Diagnostic(
        severity=finding.severity,
        message=finding.message,
        source_path=finding.target,
        rule_id=finding.rule_id,
        next_action=finding.next_action,
    )


def _run_check(args: argparse.Namespace) -> CommandResult:
    chirp_result = None
    if not args.content_only and not args.agent_only:
        _ensure_pythonpath()
        chirp_result = _run_chirp_app_check(args)
    else:
        _ensure_pythonpath()
        sys.path.insert(0, str(_app_root(args)))
    if args.agent_only:
        errors: list[str] = []
        warnings: list[str] = []
        dcp_errors: list[str] = []
        dcp_file_count = 0
    else:
        errors, warnings = _run_docs_content_check(
            args=args,
            warnings_as_errors=args.warnings_as_errors,
            strict_views=args.deploy or args.warnings_as_errors,
            strict_edition_links=args.strict_edition_links or args.deploy,
        )
        dcp_errors, dcp_file_count = _run_dcp_file_checks(args)
        errors.extend(dcp_errors)
    agent_errors = []
    agent_warnings = []
    stale_public_outputs = tuple(
        message for message in [*errors, *warnings] if "public output is stale" in message
    )
    if args.agent or args.agent_only:
        agent_errors, agent_warnings = _run_agent_checks(
            args,
            include_safety=bool(args.agent and not args.agent_only),
            stale_public_outputs=stale_public_outputs,
        )
    diagnostics: list[Diagnostic] = []
    chirp_issues = tuple(chirp_result.issues) if chirp_result is not None else ()
    chirp_errors = tuple(issue for issue in chirp_issues if issue.severity.value == "error")
    chirp_warnings = tuple(issue for issue in chirp_issues if issue.severity.value == "warning")
    chirp_infos = tuple(issue for issue in chirp_issues if issue.severity.value == "info")
    diagnostics.extend(_chirp_diagnostic(issue) for issue in chirp_issues)
    diagnostics.extend(
        diagnostic_from_message(
            message,
            severity="error",
            rule_id=_content_rule_id(message, dcp_errors=dcp_errors),
            next_action=(
                "Update the sample export or schema version and rerun fura check."
                if message in dcp_errors
                else "Refresh frozen public output with fura freeze or fura export --fresh."
                if "public output is stale" in message
                else "Fix the content validation error and rerun fura check."
            ),
        )
        for message in errors
    )
    diagnostics.extend(
        diagnostic_from_message(
            message,
            severity="warning",
            rule_id=_content_rule_id(message, dcp_errors=dcp_errors),
            next_action=(
                "Refresh frozen public output with fura freeze or fura export --fresh."
                if "public output is stale" in message
                else "Review the warning or run without --warnings-as-errors."
            ),
        )
        for message in warnings
    )
    diagnostics.extend(_agent_diagnostic(finding) for finding in agent_errors)
    diagnostics.extend(_agent_diagnostic(finding) for finding in agent_warnings)
    has_error = bool(chirp_errors or errors or agent_errors)
    warnings_fail = bool((warnings or agent_warnings) and args.warnings_as_errors) or bool(
        chirp_warnings and (args.warnings_as_errors or args.deploy)
    )
    exit_code = (
        ExitCode.VALIDATION_ERROR
        if has_error
        else ExitCode.WARNING
        if warnings_fail
        else ExitCode.SUCCESS
    )
    total_errors = len(chirp_errors) + len(errors) + len(agent_errors)
    total_warnings = len(chirp_warnings) + len(warnings) + len(agent_warnings)
    total_infos = len(chirp_infos)
    result = CommandResult(
        command=command_name(args),
        ok=exit_code == ExitCode.SUCCESS,
        exit_code=exit_code,
        summary=(
            "check completed with "
            f"{total_errors} error(s), {total_warnings} warning(s), and {total_infos} info finding(s)"
        ),
        diagnostics=tuple(diagnostics),
        data={
            "error_count": total_errors,
            "warning_count": total_warnings,
            "info_count": total_infos,
            "chirp_error_count": len(chirp_errors),
            "chirp_warning_count": len(chirp_warnings),
            "chirp_info_count": len(chirp_infos),
            "chirp_routes_checked": (
                chirp_result.routes_checked if chirp_result is not None else 0
            ),
            "chirp_templates_scanned": (
                chirp_result.templates_scanned if chirp_result is not None else 0
            ),
            "content_only": bool(args.content_only),
            "agent": bool(args.agent or args.agent_only),
            "agent_only": bool(args.agent_only),
            "agent_error_count": len(agent_errors),
            "agent_warning_count": len(agent_warnings),
            "deploy": bool(args.deploy),
            "warnings_as_errors": bool(args.warnings_as_errors),
            "strict_edition_links": bool(args.strict_edition_links),
            "dcp_file_count": dcp_file_count,
            "dcp_fixtures": bool(getattr(args, "dcp_fixtures", False)),
        },
    )
    if args.report_format:
        from furatena.cli.reports import render_report

        return replace(result, terminal_lines=(render_report(result, args.report_format),))
    terminal_lines = [result.summary]
    for diagnostic in result.diagnostics:
        location = diagnostic.source_path or ""
        if diagnostic.line is not None:
            location = f"{location}:{diagnostic.line}" if location else str(diagnostic.line)
        prefix = f"{location}: " if location else ""
        terminal_lines.append(f"{diagnostic.severity}: {prefix}{diagnostic.message}")
        if diagnostic.next_action:
            terminal_lines.append(f"  next: {diagnostic.next_action}")
    return replace(result, terminal_lines=tuple(terminal_lines))


def configure(sub: Any) -> None:
    check = sub.add_parser("check", help="Run Chirp contract + content checks")
    check.add_argument("app", nargs="?", default=None, help="Optional app import string")
    check.add_argument("--content-only", action="store_true")
    check.add_argument(
        "--agent", action="store_true", help="Include agent MCP/resource contract lint"
    )
    check.add_argument(
        "--agent-only",
        action="store_true",
        help="Run only agent MCP/resource contract lint",
    )
    check.add_argument("--warnings-as-errors", action="store_true")
    check.add_argument("--deploy", action="store_true")
    check.add_argument("--strict-edition-links", action="store_true")
    check.add_argument(
        "--dcp-file",
        action="append",
        default=[],
        help="Validate an exported DCP catalog JSON file; may be repeated",
    )
    check.add_argument(
        "--dcp-fixtures",
        action="store_true",
        help="Validate bundled DCP compatibility fixture exports",
    )
    check.add_argument(
        "--report-format",
        choices=("github", "junit", "checkstyle", "markdown"),
        default=None,
        help="Emit a CI report format instead of human-readable output",
    )
    check.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    check.set_defaults(handler=_run_check)


COMMAND = CommandModule("check", configure)
