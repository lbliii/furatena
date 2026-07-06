"""export command parser and execution."""

from __future__ import annotations

import argparse
import os
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
from furatena.cli.contracts import CommandResult, ExitCode, command_name, diagnostic_from_message


def _run_export(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    if args.base_url:
        os.environ["FURA_BASE_URL"] = args.base_url
    if args.base_path:
        os.environ["FURA_BASE_PATH"] = args.base_path
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.freeze import FreezeCatalogOptions, freeze_catalog
    from furatena.catalog.runtime import ServeConfig, ServeMode
    from furatena.catalog.static_export import (
        StaticExportLifecycleError,
        StaticExportOptions,
        export_static_site,
        normalize_base_path,
    )
    from furatena.catalog.visibility_audit import StaticExportVisibilityError

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    output = Path(args.output).expanduser().resolve() if args.output else app_root / "public"
    frozen = Path(args.frozen).expanduser().resolve() if args.frozen else app_root / "frozen"
    if args.fresh or not (frozen / "catalog.json").is_file():
        freeze_catalog(
            FreezeCatalogOptions(
                docs_config=_docs_yaml(args),
                app_root=app_root,
                repo_root=repo_root,
                output_dir=frozen,
                full_rebuild=args.fresh,
                autodoc=True,
                autodoc_config=_autodoc_config(args, repo_root),
            )
        )
    docs = DocsApp.from_paths(
        _docs_yaml(args),
        repo_root=repo_root,
        autodoc_config=_autodoc_config(args, repo_root),
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, frozen, True, False),
    )
    options = StaticExportOptions(
        output_dir=output,
        base_path=normalize_base_path(args.base_path or ""),
        site_url=args.base_url.rstrip("/") if args.base_url else None,
        frozen_dir=frozen,
        include_index_txt=not args.no_index_txt,
        incremental=args.incremental,
        allow_lifecycle_errors=args.allow_lifecycle_errors,
    )
    try:
        result = export_static_site(docs, options)
    except StaticExportLifecycleError as exc:
        diagnostics = tuple(
            diagnostic_from_message(
                message,
                severity="error",
                rule_id="fura.lifecycle",
                next_action="Fix lifecycle front matter or rerun with --allow-lifecycle-errors.",
            )
            for message in exc.errors
        )
        if _json_output(args):
            _finish_result(
                CommandResult(
                    command=command_name(args),
                    ok=False,
                    exit_code=ExitCode.VALIDATION_ERROR,
                    summary="static export blocked by lifecycle safety checks",
                    diagnostics=diagnostics,
                    data={
                        "error_count": len(exc.errors),
                        "warning_count": len(exc.warnings),
                        "allow_lifecycle_errors": False,
                    },
                ),
                json_output=True,
            )
            return
        for diagnostic in diagnostics:
            print(f"error: {diagnostic.message}")
        raise SystemExit(int(ExitCode.VALIDATION_ERROR)) from exc
    except StaticExportVisibilityError as exc:
        diagnostics = tuple(
            diagnostic_from_message(
                finding.format(),
                severity="error",
                rule_id="fura.visibility_leak",
                next_action=f"Remove the leaked {finding.boundary} content from public outputs.",
            )
            for finding in exc.report.findings
        )
        if _json_output(args):
            _finish_result(
                CommandResult(
                    command=command_name(args),
                    ok=False,
                    exit_code=ExitCode.VALIDATION_ERROR,
                    summary="static export blocked by public visibility leak",
                    diagnostics=diagnostics,
                    data={
                        "leak_count": len(exc.report.findings),
                        "canary_count": len(exc.report.canaries),
                        "scanned_artifacts": exc.report.scanned_artifacts,
                    },
                ),
                json_output=True,
            )
            return
        for diagnostic in diagnostics:
            print(f"error: {diagnostic.message}")
        raise SystemExit(int(ExitCode.VALIDATION_ERROR)) from exc
    base = options.base_path or "/"
    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary="static export completed",
                data={
                    "output_dir": result.output_dir,
                    "page_count": result.page_count,
                    "sidecar_count": result.sidecar_count,
                    "asset_mounts": result.asset_mounts,
                    "skipped_count": result.skipped_count,
                    "base_path": base,
                    "frozen_dir": frozen,
                    "fresh": bool(args.fresh),
                    "incremental": bool(args.incremental),
                    "visibility_canary_count": result.visibility_canary_count,
                    "visibility_scanned_artifacts": result.visibility_scanned_artifacts,
                },
            ),
            json_output=True,
        )
        return
    print(
        f"Exported {result.page_count} pages + {result.sidecar_count} sidecars "
        f"to {result.output_dir} (base_path={base}, skipped={result.skipped_count}, "
        f"visibility={result.visibility_canary_count} canaries/"
        f"{result.visibility_scanned_artifacts} artifacts)"
    )


def configure(sub: Any) -> None:
    export = sub.add_parser("export", help="Link frozen catalog into static HTML")
    export.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Output directory (default app/public)",
    )
    export.add_argument(
        "--frozen",
        default=None,
        help="Frozen catalog directory",
    )
    export.add_argument("--base-path", default="/chirp", help="URL path prefix")
    export.add_argument(
        "--base-url",
        default="https://lbliii.github.io/chirp",
        help="Public origin for canonical/OG URLs",
    )
    export.add_argument("--no-index-txt", action="store_true")
    export.add_argument("--incremental", action="store_true")
    export.add_argument("--fresh", action="store_true", help="Run freeze before export")
    export.add_argument(
        "--allow-lifecycle-errors",
        action="store_true",
        help="Export even when lifecycle safety checks find draft/private leaks",
    )
    export.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    export.set_defaults(handler=_run_export)


COMMAND = CommandModule("export", configure)
