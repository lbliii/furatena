"""author command parser and execution."""

from __future__ import annotations

import argparse
import getpass
import sys
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
from furatena.cli.commands.check import _run_docs_content_check
from furatena.cli.contracts import CommandResult, Diagnostic, ExitCode, command_name


def _author_diagnostics(result) -> tuple[Diagnostic, ...]:
    return tuple(
        Diagnostic(
            severity=diagnostic.severity,
            message=diagnostic.message,
            source_path=diagnostic.source_path,
            rule_id=diagnostic.rule_id,
            next_action=diagnostic.next_action,
        )
        for diagnostic in result.diagnostics
    )


def _run_public_inspection(
    args: argparse.Namespace,
    *,
    app_root,
    repo,
    config,
    mounts,
    subject,
    store,
) -> None:
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.public_projection import inspect_public_transition
    from furatena.catalog.runtime import ServeConfig, ServeMode
    from furatena.cli.authoring import author_status, author_transition

    status = author_status(
        args.target,
        mounts=mounts,
        subject=subject,
        mount_id=args.mount,
        store=store,
    )
    if not status.ok:
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=False,
                exit_code=ExitCode.CONFIG_ERROR,
                summary="author inspect-public could not resolve the source target",
                diagnostics=_author_diagnostics(status),
                data=status.to_dict(),
            ),
            json_output=_json_output(args),
        )
        return
    transition = author_transition(
        args.operation,
        args.target,
        mounts=mounts,
        subject=subject,
        expected_revision=status.source_revision,
        mount_id=args.mount,
        dry_run=True,
        confirmed=False,
        store=store,
    )
    if not transition.ok:
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=False,
                exit_code=ExitCode.VALIDATION_ERROR,
                summary="author inspect-public rejected the lifecycle transition",
                diagnostics=_author_diagnostics(transition),
                data=transition.to_dict(),
            ),
            json_output=_json_output(args),
        )
        return
    docs_app = DocsApp(
        config,
        repo_root=repo,
        autodoc=not args.no_autodoc,
        autodoc_config=_autodoc_config(args, repo),
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
        author_subject=subject,
        author_store=store,
    )
    registry = docs_app.catalog
    node = registry.get_by_slug(args.target.strip().strip("/"))
    if node is None and status.target_path is not None:
        target_path = status.target_path.resolve()
        content_roots = {mount.id: mount.content_root for mount in registry.mounts}
        node = next(
            (
                item
                for item in registry.nodes
                if (content_roots[item.mount] / item.source_path).resolve() == target_path
            ),
            None,
        )
    if node is None:
        diagnostic = Diagnostic(
            severity="error",
            message="author inspect-public could not match the source to a catalog node",
            rule_id="fura.public_projection.node_missing",
            next_action="Refresh the catalog and retry with the canonical page slug.",
        )
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=False,
                exit_code=ExitCode.CONFIG_ERROR,
                summary="author inspect-public could not load the catalog node",
                diagnostics=(diagnostic,),
                data=status.to_dict(),
            ),
            json_output=_json_output(args),
        )
        return
    payload = inspect_public_transition(
        registry,
        node,
        transition,
        current_source_revision=str(status.source_revision or ""),
        config=config,
        docs_app=docs_app,
        transport="cli",
    )
    diagnostics = tuple(
        Diagnostic(
            severity=str(item.get("severity") or "error"),
            message=str(item.get("message") or "Public projection failed."),
            rule_id=str(item.get("rule_id") or "fura.public_projection"),
            next_action=str(item.get("next_action") or "Create a fresh projection plan."),
        )
        for item in payload.get("diagnostics", ())
        if isinstance(item, dict)
    )
    result = CommandResult(
        command=command_name(args),
        ok=bool(payload.get("ok")),
        exit_code=ExitCode.SUCCESS if payload.get("ok") else ExitCode.VALIDATION_ERROR,
        summary=(
            f"projected {args.operation} across {len(payload.get('surfaces', ()))} public surfaces"
            if payload.get("ok")
            else "public projection failed closed"
        ),
        diagnostics=diagnostics,
        data=payload,
    )
    if _json_output(args):
        _finish_result(result, json_output=True)
        return
    print(result.summary)
    print(f"plan: {payload['plan']['plan_id']} ({payload['plan']['plan_digest']})")
    for surface in payload.get("surfaces", ()):
        if isinstance(surface, dict):
            print(f"{surface['change']}: {surface['id']}")
    if not result.ok:
        raise SystemExit(int(result.exit_code))


def _run_author(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    sys.path.insert(0, str(_app_root(args)))
    from furatena.catalog.access import AccessSubject
    from furatena.catalog.author_store import FilesystemAuthorMutationStore
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.registry import load_mounts
    from furatena.cli.authoring import (
        author_apply_edit,
        author_new,
        author_status,
        author_transition,
        author_validate,
    )

    app_root = _app_root(args)
    repo = _repo_for_app(app_root)
    config = load_docs_config(_docs_yaml(args))
    mounts = load_mounts(config.mounts_path or app_root / "mounts.yaml", repo_root=repo)
    mount_id = getattr(args, "mount", None)
    command = args.author_command
    subject = AccessSubject.from_values(
        actor=f"local:{getpass.getuser()}",
        roles=["admin"],
    )
    store = FilesystemAuthorMutationStore()

    if command == "inspect-public":
        _run_public_inspection(
            args,
            app_root=app_root,
            repo=repo,
            config=config,
            mounts=mounts,
            subject=subject,
            store=store,
        )
        return

    if command == "status":
        result = author_status(
            args.target, mounts=mounts, subject=subject, mount_id=mount_id, store=store
        )
    elif command == "validate":
        result = author_validate(
            args.target,
            mounts=mounts,
            subject=subject,
            mount_id=mount_id,
            store=store,
        )
        if result.ok:
            errors, warnings = _run_docs_content_check(args=args)
            result = author_validate(
                args.target,
                mounts=mounts,
                subject=subject,
                mount_id=mount_id,
                validation_errors=tuple(errors),
                validation_warnings=tuple(warnings),
                store=store,
            )
    elif command == "new":
        result = author_new(
            args.slug,
            mounts=mounts,
            subject=subject,
            mount_id=mount_id,
            title=args.title,
            dry_run=args.dry_run,
            confirmed=args.yes,
            store=store,
        )
    elif command == "edit":
        result = author_apply_edit(
            args.target,
            mounts=mounts,
            subject=subject,
            mount_id=mount_id,
            old_text=args.old_text,
            new_text=args.new_text,
            expected_revision=args.source_revision,
            dry_run=args.dry_run,
            confirmed=args.yes,
            store=store,
        )
    else:
        result = author_transition(
            command,
            args.target,
            mounts=mounts,
            subject=subject,
            expected_revision=args.source_revision,
            mount_id=mount_id,
            dry_run=args.dry_run,
            confirmed=args.yes,
            store=store,
        )

    diagnostics = _author_diagnostics(result)
    exit_code = (
        ExitCode.SUCCESS
        if result.ok
        else ExitCode.SOURCE_ERROR
        if any(diagnostic.rule_id == "fura.author.conflict" for diagnostic in result.diagnostics)
        else ExitCode.VALIDATION_ERROR
        if command == "validate" and result.target_path is not None
        else ExitCode.CONFIG_ERROR
    )
    summary = (
        f"author {command} completed"
        if result.ok
        else f"author {command} failed: {diagnostics[0].message if diagnostics else 'unknown error'}"
    )
    payload = result.to_dict()
    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=result.ok,
                exit_code=exit_code,
                summary=summary,
                diagnostics=diagnostics,
                data=payload,
            ),
            json_output=True,
        )
        return

    if result.ok:
        state = result.resulting_visibility or "unknown"
        target = result.target_path or "<none>"
        print(f"author {command}: {target} -> {state}")
        if result.dry_run:
            print("dry run: no files written")
        if result.diff:
            print(result.diff, end="" if result.diff.endswith("\n") else "\n")
        return
    for diagnostic in diagnostics:
        print(f"error: {diagnostic.message}")
    raise SystemExit(int(exit_code))


def configure(sub: Any) -> None:
    author = sub.add_parser("author", help="Local author lifecycle operations")
    author_sub = author.add_subparsers(dest="author_command", required=True)

    author_new_cmd = author_sub.add_parser("new", help="Create a draft page")
    author_new_cmd.add_argument(
        "slug", help="Page slug under the selected mount, e.g. docs/new-page"
    )
    author_new_cmd.add_argument("--title", default=None, help="Page title (default from slug)")
    author_new_cmd.add_argument("--mount", default=None, help="Mount id from mounts.yaml")
    author_new_cmd.add_argument("--dry-run", action="store_true", help="Preview without writing")
    author_new_cmd.add_argument("--yes", action="store_true", help="Confirm source mutation")
    author_new_cmd.add_argument(
        "--json", action="store_true", help="Emit the standard command result JSON"
    )
    author_new_cmd.set_defaults(handler=_run_author)

    author_status_cmd = author_sub.add_parser("status", help="Inspect lifecycle status for a page")
    author_status_cmd.add_argument("target", help="Source path or page slug")
    author_status_cmd.add_argument("--mount", default=None, help="Mount id from mounts.yaml")
    author_status_cmd.add_argument(
        "--json", action="store_true", help="Emit the standard command result JSON"
    )
    author_status_cmd.set_defaults(handler=_run_author)

    author_validate_cmd = author_sub.add_parser("validate", help="Validate a source page")
    author_validate_cmd.add_argument("target", help="Source path or page slug")
    author_validate_cmd.add_argument("--mount", default=None, help="Mount id from mounts.yaml")
    author_validate_cmd.add_argument(
        "--json", action="store_true", help="Emit the standard command result JSON"
    )
    author_validate_cmd.set_defaults(handler=_run_author)

    author_inspect_cmd = author_sub.add_parser(
        "inspect-public",
        help="Simulate every anonymous public output for an exact source transition",
    )
    author_inspect_cmd.add_argument("target", help="Source path or page slug")
    author_inspect_cmd.add_argument(
        "--operation",
        choices=("publish", "unpublish", "archive"),
        default="publish",
        help="Lifecycle transition to project without applying it",
    )
    author_inspect_cmd.add_argument("--mount", default=None, help="Mount id from mounts.yaml")
    author_inspect_cmd.add_argument(
        "--no-autodoc", action="store_true", help="Skip Python autodoc while projecting"
    )
    author_inspect_cmd.add_argument(
        "--json", action="store_true", help="Emit the standard command result JSON"
    )
    author_inspect_cmd.set_defaults(handler=_run_author)

    author_edit_cmd = author_sub.add_parser(
        "edit", help="Apply an exact-text edit to a source page"
    )
    author_edit_cmd.add_argument("target", help="Source path or page slug")
    author_edit_cmd.add_argument("--old-text", required=True, help="Exact source span to replace")
    author_edit_cmd.add_argument("--new-text", required=True, help="Replacement source text")
    author_edit_cmd.add_argument(
        "--source-revision",
        default=None,
        help="SHA-256 revision returned by author status/read",
    )
    author_edit_cmd.add_argument("--mount", default=None, help="Mount id from mounts.yaml")
    author_edit_cmd.add_argument("--dry-run", action="store_true", help="Preview without writing")
    author_edit_cmd.add_argument("--yes", action="store_true", help="Confirm source mutation")
    author_edit_cmd.add_argument(
        "--json", action="store_true", help="Emit the standard command result JSON"
    )
    author_edit_cmd.set_defaults(handler=_run_author)

    for lifecycle_command, help_text in (
        ("draft", "Mark a page as draft/private preview"),
        ("publish", "Mark a page as public"),
        ("unpublish", "Move a public page back to draft"),
        ("archive", "Archive a page"),
    ):
        item = author_sub.add_parser(lifecycle_command, help=help_text)
        item.add_argument("target", help="Source path or page slug")
        item.add_argument("--mount", default=None, help="Mount id from mounts.yaml")
        item.add_argument(
            "--source-revision",
            default=None,
            help="SHA-256 revision returned by author status/read",
        )
        item.add_argument("--dry-run", action="store_true", help="Preview without writing")
        item.add_argument("--yes", action="store_true", help="Confirm source mutation")
        item.add_argument(
            "--json", action="store_true", help="Emit the standard command result JSON"
        )
        item.set_defaults(handler=_run_author)


COMMAND = CommandModule("author", configure)
