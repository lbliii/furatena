"""freeze command parser and execution."""

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
from furatena.cli.contracts import CommandResult, command_name


def _run_freeze(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    if args.workers is not None:
        os.environ["FURA_WORKERS"] = str(args.workers)
    from furatena.catalog.application_roots import ApplicationRoots
    from furatena.catalog.freeze import FreezeCatalogOptions, freeze_catalog

    app_root = _app_root(args)
    roots = ApplicationRoots.from_environment(app_root)
    if roots.managed:
        roots.ensure_writable_roots()
    repo_root = _repo_for_app(app_root)
    output = Path(args.output).expanduser().resolve() if args.output else roots.output / "frozen"
    result = freeze_catalog(
        FreezeCatalogOptions(
            docs_config=_docs_yaml(args),
            app_root=app_root,
            repo_root=repo_root,
            output_dir=output,
            platform_root=roots.platform if roots.managed else None,
            state_root=roots.state,
            full_rebuild=args.full,
            workers=args.workers,
            autodoc=True,
            autodoc_config=_autodoc_config(args, repo_root),
        )
    )
    if _json_output(args):
        status = "updated" if result.frozen_mounts or result.frozen_editions else "up_to_date"
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary=f"freeze {status}",
                data={
                    "status": status,
                    "output_dir": result.output_dir,
                    "frozen_mounts": result.frozen_mounts,
                    "frozen_editions": result.frozen_editions,
                    "reused_editions": result.reused_editions,
                    "page_count": result.page_count,
                    "index_seconds": round(result.index_seconds, 3),
                    "export_seconds": round(result.export_seconds, 3),
                    "edition_seconds": round(result.edition_seconds, 3),
                    "worker_count": result.worker_count,
                },
            ),
            json_output=True,
        )
        return
    if result.frozen_mounts or result.frozen_editions:
        print(
            f"Froze {len(result.frozen_mounts)} mount(s) and "
            f"{len(result.frozen_editions)} edition(s), {result.page_count} latest pages total -> "
            f"{result.output_dir} (index {result.index_seconds:.1f}s, "
            f"editions {result.edition_seconds:.1f}s, export {result.export_seconds:.1f}s, "
            f"{result.worker_count} workers)"
        )
    else:
        print(
            f"Freeze up to date - {result.page_count} pages at {result.output_dir} "
            f"(index {result.index_seconds:.1f}s, {result.worker_count} workers)"
        )


def configure(sub: Any) -> None:
    freeze = sub.add_parser("freeze", help="Export catalog JSON + HTML fragments")
    freeze.add_argument("--full", action="store_true", help="Force full rebuild")
    freeze.add_argument("--workers", type=int, default=None, help="Parallel workers")
    freeze.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    freeze.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Output directory (default FURA_OUTPUT_ROOT/frozen or APP_ROOT/frozen)",
    )
    freeze.set_defaults(handler=_run_freeze)


COMMAND = CommandModule("freeze", configure)
