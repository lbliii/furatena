"""migrate command parser and execution."""

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
from furatena.cli.commands.check import _content_rule_id
from furatena.cli.contracts import (
    CommandResult,
    Diagnostic,
    ExitCode,
    command_name,
    diagnostic_from_message,
)


def _run_migrate(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    sys.path.insert(0, str(_app_root(args)))
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.migrate import (
        migrate_mdx_paths,
        migrate_mounts,
        remediate_mdx_paths_safely,
    )
    from furatena.catalog.registry import load_mounts

    if args.report:
        result = _run_migration_report(args)
        if _json_output(args):
            _finish_result(result, json_output=True)
            return
        from furatena.catalog.migrate import render_migration_report

        print(render_migration_report(result.data["migration_report"]))
        if result.exit_code:
            raise SystemExit(int(result.exit_code))
        return

    app_root = _app_root(args)
    repo = _repo_for_app(app_root)
    config = load_docs_config(_docs_yaml(args))
    mounts_path = config.mounts_path or app_root / "mounts.yaml"
    mounts = load_mounts(mounts_path, repo_root=repo)
    if args.apply_safe:
        paths = [Path(path).expanduser().resolve() for path in args.paths]
        if not paths:
            paths = [
                path
                for mount in mounts
                if mount.content_root.is_dir()
                for path in sorted(mount.content_root.rglob("*.mdx"))
            ]
        remediations = remediate_mdx_paths_safely(paths, write=not args.dry_run)
        manual = [item for item in remediations if not item.safe]
        diagnostics = tuple(
            Diagnostic(
                severity="warning",
                source_path=str(item.source_path),
                message=item.reason,
                rule_id="fura.migration.remediation.manual",
                next_action="Assign an owner and resolve this source-specific blocker manually.",
            )
            for item in manual
        )
        exit_code = ExitCode.WARNING if manual else ExitCode.SUCCESS
        result = CommandResult(
            command=command_name(args),
            ok=not manual,
            exit_code=exit_code,
            summary=(
                f"safe migration remediation processed {len(remediations)} source(s): "
                f"{sum(item.status == 'applied' for item in remediations)} applied, "
                f"{sum(item.status == 'planned' for item in remediations)} planned, "
                f"{sum(item.status == 'unchanged' for item in remediations)} unchanged, "
                f"{len(manual)} manual"
            ),
            diagnostics=diagnostics,
            data={
                "apply_safe": True,
                "dry_run": bool(args.dry_run),
                "source_preservation": "required",
                "overwrite_existing": False,
                "count": len(remediations),
                "manual_count": len(manual),
                "remediations": [item.to_dict() for item in remediations],
            },
            terminal_lines=(
                *(
                    f"{item.status}: {item.source_path} -> {item.target_path}: {item.reason}"
                    for item in remediations
                ),
                f"manual blockers: {len(manual)}",
            ),
        )
        _finish_result(result, json_output=_json_output(args))
        return
    write = not args.dry_run
    remove_source = not args.keep_mdx

    if args.paths:
        paths = [Path(p).resolve() for p in args.paths]
        reports = migrate_mdx_paths(paths, write=write, remove_source=remove_source)
    else:
        reports = migrate_mounts(mounts, write=write, remove_source=remove_source)

    if not reports:
        if _json_output(args):
            _finish_result(
                CommandResult(
                    command=command_name(args),
                    ok=True,
                    summary="no MDX files found",
                    data={
                        "reports": [],
                        "count": 0,
                        "dry_run": bool(args.dry_run),
                        "keep_mdx": bool(args.keep_mdx),
                    },
                ),
                json_output=True,
            )
            return
        print("No MDX files found.")
        return

    errors = 0
    diagnostics: list[Diagnostic] = []
    report_payloads: list[dict[str, object]] = []
    for report in reports:
        report_payloads.append(
            {
                "source_path": report.source_path,
                "target_path": report.target_path,
                "changed": report.changed,
                "written": report.written,
                "removed_source": report.removed_source,
                "unmigrated_components": report.unmigrated_components,
                "warning_count": len(report.warnings) + len(report.unmigrated_components),
                "error_count": len(report.errors),
            }
        )
        if report.errors:
            errors += 1
            diagnostics.extend(
                Diagnostic(
                    severity="error",
                    source_path=str(report.source_path),
                    message=message,
                    rule_id="fura.migrate",
                    next_action="Fix the source path or MDX input and rerun fura migrate.",
                )
                for message in report.errors
            )
            if _json_output(args):
                continue
            for message in report.errors:
                print(f"error: {report.source_path}: {message}")
            continue
        if not report.changed and not report.written:
            if _json_output(args):
                continue
            print(f"skip {report.source_path} (already canonical)")
            continue
        status = "would write" if not write else "migrated"
        if not _json_output(args):
            print(f"{status} {report.source_path} -> {report.target_path}")
        for component in report.unmigrated_components:
            diagnostics.append(
                Diagnostic(
                    severity="warning",
                    source_path=str(report.source_path),
                    message=f"unmigrated JSX component {component}",
                    rule_id="fura.migrate.unmigrated_component",
                    next_action="Replace or manually port the remaining JSX component.",
                )
            )
            if not _json_output(args):
                print(f"warning: {report.source_path}: unmigrated JSX component {component}")
        for message in report.warnings:
            diagnostics.append(
                Diagnostic(
                    severity="warning",
                    source_path=str(report.source_path),
                    message=message,
                    rule_id="fura.migrate",
                    next_action="Review the migrated markdown output.",
                )
            )
            if not _json_output(args):
                print(f"warning: {report.source_path}: {message}")

    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=errors == 0,
                exit_code=ExitCode.VALIDATION_ERROR if errors else ExitCode.SUCCESS,
                summary=f"processed {len(reports)} MDX migration report(s)",
                diagnostics=tuple(diagnostics),
                data={
                    "reports": report_payloads,
                    "count": len(reports),
                    "changed_count": sum(1 for report in reports if report.changed),
                    "written_count": sum(1 for report in reports if report.written),
                    "dry_run": bool(args.dry_run),
                    "keep_mdx": bool(args.keep_mdx),
                },
            ),
            json_output=True,
        )
        return
    if errors:
        raise SystemExit(1)


def _run_migration_report(args: argparse.Namespace) -> CommandResult:
    from furatena.catalog.check import check_catalog
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.migrate import build_migration_report
    from furatena.catalog.registry import CatalogRegistry
    from furatena.catalog.theme import DocsTheme
    from furatena.catalog.views import ViewRegistry

    app_root = _app_root(args)
    repo = _repo_for_app(app_root)
    config = load_docs_config(_docs_yaml(args))
    theme = DocsTheme.from_docs_config(config)
    mounts_path = config.mounts_path or app_root / "mounts.yaml"
    registry = CatalogRegistry.from_config(
        mounts_path,
        repo_root=repo,
        app_root=app_root,
        rewrites_path=config.rewrites_path,
        inventories_path=config.inventories_path,
        catalog_identity=config.identity.to_meta(),
        autodoc=False,
        autodoc_config=_autodoc_config(args, repo),
        include_private=True,
    )
    views = ViewRegistry(config)
    errors, warnings = check_catalog(
        registry,
        views=views,
        docs=config,
        theme=theme,
        strict_views=False,
        strict_edition_links=False,
        inventory_store=registry.inventory_store,
    )
    diagnostics = [
        diagnostic_from_message(
            message,
            severity="error",
            rule_id=_content_rule_id(message, dcp_errors=[]),
            next_action="Fix this migration blocker before switching platforms.",
        )
        for message in errors
    ]
    diagnostics.extend(
        diagnostic_from_message(
            message,
            severity="warning",
            rule_id=_content_rule_id(message, dcp_errors=[]),
            next_action="Review this migration risk before switching platforms.",
        )
        for message in warnings
    )
    report = build_migration_report(registry, diagnostics=tuple(diagnostics))
    finding_diagnostics = tuple(
        Diagnostic(
            severity=str(finding["severity"]),
            source_path=str(finding["source_path"]),
            line=finding.get("line"),
            message=str(finding["message"]),
            rule_id=str(finding.get("rule_id") or "fura.migration.report"),
            next_action=str(finding["next_action"]),
        )
        for finding in report["findings"]
        if finding["severity"] in {"error", "warning"}
    )
    summary = report["summary"]
    exit_code = ExitCode.VALIDATION_ERROR if summary["error_count"] else ExitCode.SUCCESS
    return CommandResult(
        command=command_name(args),
        ok=exit_code == ExitCode.SUCCESS,
        exit_code=exit_code,
        summary=(
            "migration report completed with "
            f"{summary['error_count']} error(s), "
            f"{summary['warning_count']} warning(s), "
            f"{summary['info_count']} info"
        ),
        diagnostics=finding_diagnostics,
        data={
            "migration_report": report,
            "report": True,
            "dry_run": True,
            "include_private": True,
        },
    )


def configure(sub: Any) -> None:
    migrate = sub.add_parser("migrate", help="Lower MDX JSX to Patitas directives")
    migrate.add_argument("paths", nargs="*", help="Optional .mdx files")
    mode = migrate.add_mutually_exclusive_group()
    mode.add_argument(
        "--report",
        action="store_true",
        help="Report migration readiness risks without writing files",
    )
    mode.add_argument(
        "--apply-safe",
        action="store_true",
        help="Create reversible canonical siblings only for conflict-free MDX conversions",
    )
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("--keep-mdx", action="store_true")
    migrate.add_argument(
        "--json", action="store_true", help="Emit the standard command result JSON"
    )
    migrate.set_defaults(handler=_run_migrate)


COMMAND = CommandModule("migrate", configure)
