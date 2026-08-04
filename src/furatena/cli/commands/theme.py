"""theme command parser and execution."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from furatena.cli.commands._shared import (
    CommandModule,
    _app_root,
    _docs_yaml,
    _ensure_pythonpath,
    _finish_result,
    _json_output,
)
from furatena.cli.contracts import CommandResult, command_name


def _run_theme_list(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    from furatena.catalog.docs_core import list_docs_core_ids, load_docs_core
    from furatena.catalog.theme_pack import list_theme_packs, load_theme_pack

    docs_core: list[dict[str, str]] = []
    for theme_id in list_docs_core_ids():
        pack = load_docs_core(theme_id)
        if pack is not None:
            docs_core.append({"id": theme_id, "root": str(pack.root)})
    skin_packs: list[dict[str, str]] = []
    for name in list_theme_packs():
        pack = load_theme_pack(name)
        skin_packs.append({"name": name, "root": str(pack.root)})
    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary=f"found {len(docs_core)} docs-core theme(s) and {len(skin_packs)} skin pack(s)",
                data={"docs_core": docs_core, "skin_packs": skin_packs},
            ),
            json_output=True,
        )
        return
    print("docs-core (theme.id):")
    for item in docs_core:
        print(f"  {item['id']}\t{item['root']}")
    print("skin packs (theme.use):")
    if not skin_packs:
        print("  (none registered)")
        return
    for item in skin_packs:
        print(f"  {item['name']}\t{item['root']}")


def _run_theme_inspect(args: argparse.Namespace) -> None:
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.theme_customization import (
        list_theme_customizations,
        resolve_theme_customization,
    )

    docs = load_docs_config(_docs_yaml(args))
    resolutions = (
        (resolve_theme_customization(docs, args.path),)
        if args.path
        else list_theme_customizations(docs)
    )
    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary=f"resolved {len(resolutions)} theme file(s)",
                data={
                    "app_root": docs.root,
                    "docs_core": docs.theme.id,
                    "skin": docs.theme.use,
                    "project_overrides": docs.templates_dir,
                    "theme_root": docs.theme_dir,
                    "files": [
                        {
                            "logical_path": resolution.logical_path,
                            "overridden": resolution.overridden,
                            "active": None
                            if resolution.active is None
                            else {
                                "label": resolution.active.label,
                                "path": resolution.active.path,
                            },
                            "override_path": resolution.override_path,
                            "candidates": [
                                {
                                    "label": candidate.label,
                                    "path": candidate.path,
                                    "exists": candidate.exists,
                                    "active": candidate.active,
                                }
                                for candidate in resolution.candidates
                            ],
                        }
                        for resolution in resolutions
                    ],
                },
            ),
            json_output=True,
        )
        return
    print("Resolved theme:")
    print(f"  app root: {docs.root}")
    print(f"  docs-core: {docs.theme.id}")
    print(f"  skin: {docs.theme.use or '(none)'}")
    print(f"  project overrides: {docs.templates_dir}")
    print(f"  theme root: {docs.theme_dir}")
    print()
    print("Files:")
    for resolution in resolutions:
        active = resolution.active
        source = f"{active.label}\t{active.path}" if active is not None else "missing"
        marker = "override" if resolution.overridden else ""
        print(f"  {resolution.logical_path}\t{source}{f'  {marker}' if marker else ''}")
        if args.verbose or args.path:
            for candidate in resolution.candidates:
                status = (
                    "active" if candidate.active else "exists" if candidate.exists else "missing"
                )
                print(f"    {candidate.label:<14} {status:<7} {candidate.path}")
            print(f"    eject target   {resolution.override_path}")


def _run_theme_eject(args: argparse.Namespace) -> None:
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.theme_customization import eject_theme_path

    if not args.path and not args.all:
        raise SystemExit("theme eject requires a path or --all")
    if args.path and args.all:
        raise SystemExit("theme eject accepts either a path or --all, not both")
    docs = load_docs_config(_docs_yaml(args))
    paths = [args.path]
    if args.all:
        from furatena.catalog.theme_customization import list_theme_customizations

        paths = [item.logical_path for item in list_theme_customizations(docs)]
    copied = 0
    skipped = 0
    payload: list[dict[str, object]] = []
    for path in paths:
        result = eject_theme_path(docs, path, force=args.force)
        copied += result.copied_files
        payload.append(
            {
                "logical_path": result.logical_path,
                "source_path": result.source_path,
                "target_path": result.target_path,
                "copied_files": result.copied_files,
                "skipped": result.skipped,
            }
        )
        if result.skipped:
            skipped += 1
            if _json_output(args):
                continue
            print(f"skip {result.logical_path} (already local at {result.target_path})")
            continue
        if _json_output(args):
            continue
        print(f"ejected {result.logical_path}")
        print(f"  from {result.source_path}")
        print(f"  to   {result.target_path}")
    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary=f"wrote {copied} file(s), skipped {skipped}",
                data={
                    "files": payload,
                    "copied": copied,
                    "skipped": skipped,
                    "force": bool(args.force),
                },
            ),
            json_output=True,
        )
        return
    print(f"wrote {copied} file(s), skipped {skipped}")


def _run_theme_diff(args: argparse.Namespace) -> None:
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.theme_customization import diff_theme_path

    docs = load_docs_config(_docs_yaml(args))
    diff = diff_theme_path(docs, args.path)
    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary="theme override differs" if diff else "theme override matches upstream",
                data={"path": args.path, "different": bool(diff), "diff": diff},
            ),
            json_output=True,
        )
        return
    if diff:
        print(diff, end="")
    else:
        print(f"no differences for {args.path}")


def _run_theme_init(args: argparse.Namespace) -> None:
    from furatena.catalog.theme_init import init_theme_pack

    target = Path(args.directory)
    if not target.is_absolute():
        target = _app_root(args) / target
    written = init_theme_pack(target, force=args.force)
    if not written:
        if _json_output(args):
            _finish_result(
                CommandResult(
                    command=command_name(args),
                    ok=True,
                    summary=f"no files written at {target}",
                    data={"target": target, "written": [], "force": bool(args.force)},
                ),
                json_output=True,
            )
            return
        print(f"no files written — {target} already initialized (use --force to overwrite)")
        return
    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary=f"initialized skin scaffold at {target}",
                data={
                    "target": target,
                    "written": [path.relative_to(target) for path in written],
                    "count": len(written),
                    "force": bool(args.force),
                },
            ),
            json_output=True,
        )
        return
    print(f"initialized skin scaffold at {target} ({len(written)} files)")
    for path in written:
        print(f"  {path.relative_to(target)}")


def configure(sub: Any) -> None:
    theme = sub.add_parser("theme", help="List installable theme packs")
    theme_sub = theme.add_subparsers(dest="theme_command", required=True)
    theme_list = theme_sub.add_parser("list", help="Show furatena.themes entry points")
    theme_list.add_argument(
        "--json", action="store_true", help="Emit the standard command result JSON"
    )
    theme_list.set_defaults(handler=_run_theme_list)
    theme_inspect = theme_sub.add_parser("inspect", help="Show resolved theme templates/assets")
    theme_inspect.add_argument(
        "path", nargs="?", default=None, help="Logical path, e.g. views/doc.html"
    )
    theme_inspect.add_argument("--verbose", action="store_true", help="Show full resolution chain")
    theme_inspect.add_argument(
        "--json", action="store_true", help="Emit the standard command result JSON"
    )
    theme_inspect.set_defaults(handler=_run_theme_inspect)
    theme_eject = theme_sub.add_parser(
        "eject", help="Copy a resolved theme file into local overrides"
    )
    theme_eject.add_argument(
        "path", nargs="?", default=None, help="Logical path, e.g. views/doc.html"
    )
    theme_eject.add_argument(
        "--all", action="store_true", help="Eject every inspectable template/asset"
    )
    theme_eject.add_argument("--force", action="store_true", help="Overwrite existing local files")
    theme_eject.add_argument(
        "--json", action="store_true", help="Emit the standard command result JSON"
    )
    theme_eject.set_defaults(handler=_run_theme_eject)
    theme_diff = theme_sub.add_parser("diff", help="Diff a local override against upstream")
    theme_diff.add_argument("path", help="Logical path, e.g. views/doc.html")
    theme_diff.add_argument(
        "--json", action="store_true", help="Emit the standard command result JSON"
    )
    theme_diff.set_defaults(handler=_run_theme_diff)
    theme_init = theme_sub.add_parser("init", help="Scaffold a project skin pack directory")
    theme_init.add_argument(
        "directory",
        nargs="?",
        default="theme-skin",
        help="Output directory relative to the app root (default: theme-skin)",
    )
    theme_init.add_argument(
        "--force", action="store_true", help="Overwrite existing scaffold files"
    )
    theme_init.add_argument(
        "--json", action="store_true", help="Emit the standard command result JSON"
    )
    theme_init.set_defaults(handler=_run_theme_init)


COMMAND = CommandModule("theme", configure)
