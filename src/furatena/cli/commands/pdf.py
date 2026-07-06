"""pdf command parser and execution."""

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
from furatena.cli.contracts import CommandResult, Diagnostic, ExitCode, command_name


def _run_pdf(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    if args.base_url:
        os.environ["FURA_BASE_URL"] = args.base_url
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.pdf_export import PDFExportOptions, export_pdfs
    from furatena.catalog.runtime import ServeConfig, ServeMode

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    output = (
        Path(args.output).expanduser().resolve() if args.output else app_root / "public" / "pdf"
    )
    docs = DocsApp.from_paths(
        _docs_yaml(args),
        repo_root=repo_root,
        autodoc_config=_autodoc_config(args, repo_root),
        autodoc=not args.no_autodoc,
        serve=ServeConfig(ServeMode.PREVIEW, None, True, False),
    )
    try:
        result = export_pdfs(
            docs.catalog,
            config=docs.config,
            options=PDFExportOptions(
                output_dir=output,
                page=args.page,
                collection=args.collection,
                site_name=docs.config.site.name,
                base_url=args.base_url.rstrip("/") if args.base_url else "",
                update_channel_manifest=not args.no_channels,
            ),
        )
    except ValueError as exc:
        diagnostic = Diagnostic(
            severity="error",
            message=str(exc),
            rule_id="fura.pdf",
            next_action="Choose a public page, collection, or omit both flags for a full-site PDF.",
        )
        if _json_output(args):
            _finish_result(
                CommandResult(
                    command=command_name(args),
                    ok=False,
                    exit_code=ExitCode.CONFIG_ERROR,
                    summary="pdf export failed",
                    diagnostics=(diagnostic,),
                    data={"output_dir": output},
                ),
                json_output=True,
            )
            return
        print(f"error: {diagnostic.message}")
        raise SystemExit(int(ExitCode.CONFIG_ERROR)) from exc
    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary="pdf export completed",
                data={
                    "output_dir": result.output_dir,
                    "target": result.target,
                    "paths": list(result.paths),
                    "page_count": result.page_count,
                    "byte_count": result.byte_count,
                    "channels_updated": not args.no_channels,
                },
            ),
            json_output=True,
        )
        return
    paths = ", ".join(str(path) for path in result.paths)
    print(f"Exported {result.page_count} page(s) to PDF: {paths}")


def configure(sub: Any) -> None:
    pdf = sub.add_parser("pdf", help="Export catalog pages to PDF artifacts")
    pdf_scope = pdf.add_mutually_exclusive_group()
    pdf_scope.add_argument("--page", default=None, help="Page URL or slug to export")
    pdf_scope.add_argument(
        "--collection", default=None, help="Collection, section, or mount to export"
    )
    pdf.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Output directory (default app/public/pdf)",
    )
    pdf.add_argument("--base-url", default="", help="Public origin for channel manifest URLs")
    pdf.add_argument("--no-autodoc", action="store_true", help="Skip autodoc slice")
    pdf.add_argument(
        "--no-channels",
        action="store_true",
        help="Do not refresh channels.json with generated PDF artifacts",
    )
    pdf.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    pdf.set_defaults(handler=_run_pdf)


COMMAND = CommandModule("pdf", configure)
