"""Fura — CLI for Furatena."""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
from pathlib import Path
from textwrap import dedent

from furatena.cli.contracts import (
    CommandResult,
    Diagnostic,
    ExitCode,
    command_name,
    diagnostic_from_message,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_app_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "docs.yaml").is_file():
        return cwd
    if (cwd / "app" / "docs.yaml").is_file():
        return cwd / "app"
    repo_app = _repo_root() / "app"
    if (repo_app / "docs.yaml").is_file():
        return repo_app
    return cwd


def _app_root(args: argparse.Namespace | None = None) -> Path:
    raw = getattr(args, "app_root", None) if args is not None else None
    raw = raw or os.environ.get("FURA_APP_ROOT")
    return Path(raw).expanduser().resolve() if raw else _default_app_root().resolve()


def _docs_yaml(args: argparse.Namespace | None = None) -> Path:
    raw = getattr(args, "config", None) if args is not None else None
    return Path(raw).expanduser().resolve() if raw else _app_root(args) / "docs.yaml"


def _repo_for_app(app_root: Path) -> Path:
    return app_root.parent if app_root.name == "app" else app_root


def _autodoc_config(args: argparse.Namespace | None, repo_root: Path) -> Path | None:
    raw = getattr(args, "autodoc_config", None) if args is not None else None
    path = Path(raw).expanduser().resolve() if raw else repo_root / "config" / "autodoc.yaml"
    return path if path.is_file() else None


def _ensure_pythonpath() -> None:
    repo = _repo_root()
    src = str(repo / "src")
    app = str(_default_app_root())
    existing = os.environ.get("PYTHONPATH", "")
    parts = [p for p in (src, app, existing) if p]
    os.environ["PYTHONPATH"] = os.pathsep.join(parts)


def _json_output(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _finish_result(result: CommandResult, *, json_output: bool) -> None:
    if json_output:
        result.write_json()
    if result.exit_code:
        raise SystemExit(int(result.exit_code))


def _coerce_exit_code(code: object) -> int:
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return int(ExitCode.INTERNAL_ERROR)


def _run_serve(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    if args.author:
        os.environ["FURA_MODE"] = "author"
    elif args.preview or args.frozen:
        os.environ["FURA_MODE"] = "preview"
    elif args.hybrid:
        os.environ["FURA_MODE"] = "hybrid"
    if args.no_autodoc:
        os.environ["FURA_AUTODOC"] = "0"
    if args.channel:
        os.environ["FURA_CHANNEL"] = args.channel
    if args.base_url:
        os.environ["FURA_BASE_URL"] = args.base_url
    if args.port:
        os.environ["FURA_PORT"] = str(args.port)
    if args.workers is not None:
        os.environ["FURA_WORKERS"] = str(args.workers)

    from furatena.catalog.config import load_docs_config
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.registry import load_mounts
    from furatena.catalog.runtime import ServeMode, resolve_serve_config

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    docs_yaml = _docs_yaml(args)
    if not docs_yaml.is_file():
        raise SystemExit(f"docs config not found: {docs_yaml}")
    config = load_docs_config(docs_yaml)
    mounts = load_mounts(config.mounts_path or app_root / "mounts.yaml", repo_root=repo_root)
    mode = None
    if args.author:
        mode = ServeMode.AUTHOR
    elif args.preview or args.frozen:
        mode = ServeMode.PREVIEW
    elif args.hybrid:
        mode = ServeMode.HYBRID
    serve = resolve_serve_config(
        docs_root=app_root,
        content_roots=tuple(mount.content_root for mount in mounts),
        mode=mode,
        frozen_dir=app_root / "frozen",
        env_frozen=bool(os.environ.get("FURA_FROZEN")),
    )
    docs = DocsApp.from_paths(
        docs_yaml,
        repo_root=repo_root,
        autodoc_config=_autodoc_config(args, repo_root),
        serve=serve,
        workers=args.workers,
    )

    port = args.port or int(os.environ.get("FURA_PORT", "8001"))
    host = args.host or "127.0.0.1"
    url = f"http://{host}:{port}/"
    from furatena.catalog.dev_banner import format_serve_startup

    for line in format_serve_startup(
        docs.serve,
        page_count=len(docs.catalog.nodes),
        mount_count=len(docs.catalog.mounts),
        url=url,
    ):
        if not _json_output(args):
            print(line)
    if _json_output(args):
        mode_name = docs.serve.mode.value if hasattr(docs.serve.mode, "value") else str(docs.serve.mode)
        CommandResult(
            command=command_name(args),
            ok=True,
            summary="serve startup completed",
            data={
                "url": url,
                "host": host,
                "port": port,
                "mode": mode_name,
                "page_count": len(docs.catalog.nodes),
                "mount_count": len(docs.catalog.mounts),
                "frozen_dir": docs.serve.frozen_dir,
                "workers": args.workers,
            },
        ).write_json()
    docs.run_serve(port=port, host=host)


def _run_stop(args: argparse.Namespace) -> None:
    from furatena.catalog.dev_reload import stop_dev_server

    host = args.host or "127.0.0.1"
    port = args.port or int(os.environ.get("FURA_PORT", "8001"))
    stopped = stop_dev_server(_repo_root(), host=host, port=port)
    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary="dev server stopped" if stopped else "no dev server was listening",
                data={"host": host, "port": port, "stopped": stopped},
            ),
            json_output=True,
        )
        return
    if stopped:
        print(f"stopped dev server on {host}:{port}")
    else:
        print(f"no dev server listening on {host}:{port}")


def _run_freeze(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    if args.workers is not None:
        os.environ["FURA_WORKERS"] = str(args.workers)
    from furatena.catalog.freeze import FreezeCatalogOptions, freeze_catalog

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    output = Path(args.output).expanduser().resolve() if args.output else app_root / "frozen"
    result = freeze_catalog(
        FreezeCatalogOptions(
            docs_config=_docs_yaml(args),
            app_root=app_root,
            repo_root=repo_root,
            output_dir=output,
            full_rebuild=args.full,
            workers=args.workers,
            autodoc=True,
            autodoc_config=_autodoc_config(args, repo_root),
        )
    )
    if _json_output(args):
        status = "updated" if result.frozen_mounts else "up_to_date"
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary=f"freeze {status}",
                data={
                    "status": status,
                    "output_dir": result.output_dir,
                    "frozen_mounts": result.frozen_mounts,
                    "page_count": result.page_count,
                    "index_seconds": round(result.index_seconds, 3),
                    "export_seconds": round(result.export_seconds, 3),
                    "worker_count": result.worker_count,
                },
            ),
            json_output=True,
        )
        return
    if result.frozen_mounts:
        print(
            f"Froze {len(result.frozen_mounts)} mount(s), {result.page_count} pages total -> "
            f"{result.output_dir} (index {result.index_seconds:.1f}s, "
            f"export {result.export_seconds:.1f}s, {result.worker_count} workers)"
        )
    else:
        print(
            f"Freeze up to date - {result.page_count} pages at {result.output_dir} "
            f"(index {result.index_seconds:.1f}s, {result.worker_count} workers)"
        )


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
                },
            ),
            json_output=True,
        )
        return
    print(
        f"Exported {result.page_count} pages + {result.sidecar_count} sidecars "
        f"to {result.output_dir} (base_path={base}, skipped={result.skipped_count})"
    )


def _run_pdf(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    if args.base_url:
        os.environ["FURA_BASE_URL"] = args.base_url
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.pdf_export import PDFExportOptions, export_pdfs
    from furatena.catalog.runtime import ServeConfig, ServeMode

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    output = Path(args.output).expanduser().resolve() if args.output else app_root / "public" / "pdf"
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


def _run_query(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    sys.path.insert(0, str(_app_root(args)))
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.query import query_catalog
    from furatena.catalog.registry import CatalogRegistry

    app_root = _app_root(args)
    repo = _repo_for_app(app_root)
    config = load_docs_config(_docs_yaml(args))
    mounts_path = config.mounts_path or app_root / "mounts.yaml"
    autodoc_config = _autodoc_config(args, repo)
    registry = CatalogRegistry.from_config(
        mounts_path,
        repo_root=repo,
        app_root=app_root,
        rewrites_path=config.rewrites_path,
        inventories_path=config.inventories_path,
        catalog_identity=config.identity.to_meta(),
        autodoc=not args.no_autodoc,
        autodoc_config=autodoc_config,
    )
    results = query_catalog(
        registry,
        directive=args.directive,
        heading=args.heading,
        mount=args.mount,
        edition=args.edition,
        tag=args.tag,
        url_prefix=args.url_prefix,
    )
    if args.json:
        exit_code = ExitCode.SUCCESS if results else ExitCode.WARNING
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=bool(results),
                exit_code=exit_code,
                summary=f"matched {len(results)} catalog node(s)",
                diagnostics=()
                if results
                else (
                    Diagnostic(
                        severity="warning",
                        message="query matched no catalog nodes",
                        next_action="Relax query filters or inspect available mounts and URLs.",
                    ),
                ),
                data={
                    "count": len(results),
                    "results": results,
                    "filters": {
                        "directive": args.directive,
                        "heading": args.heading,
                        "mount": args.mount,
                        "edition": args.edition,
                        "tag": args.tag,
                        "url_prefix": args.url_prefix,
                    },
                },
            ),
            json_output=True,
        )
        return
    else:
        for row in results:
            print(f"{row['url']}\t{row['title']}")
    if not results:
        raise SystemExit(1)


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


def _run_api_diff(args: argparse.Namespace) -> None:
    from furatena.catalog.api_governance import diff_openapi_specs

    old_path = Path(args.old).expanduser().resolve()
    new_path = Path(args.new).expanduser().resolve()
    try:
        payload = diff_openapi_specs(old_path, new_path)
    except Exception as exc:
        result = CommandResult(
            command=command_name(args),
            ok=False,
            exit_code=ExitCode.VALIDATION_ERROR,
            summary="api diff failed",
            diagnostics=(
                Diagnostic(
                    severity="error",
                    message=f"Failed to diff OpenAPI specs: {exc}",
                    rule_id="fura.api",
                    next_action="Fix the OpenAPI spec paths or YAML syntax and rerun fura api-diff.",
                ),
            ),
            data={"old_spec": str(old_path), "new_spec": str(new_path)},
        )
        _finish_result(result, json_output=_json_output(args))
        return
    result = CommandResult(
        command=command_name(args),
        ok=True,
        summary=(
            "api diff completed: "
            f"{payload['summary']['added']} added, "
            f"{payload['summary']['removed']} removed, "
            f"{payload['summary']['changed']} changed, "
            f"{payload['summary']['breaking']} breaking"
        ),
        data=payload,
    )
    if _json_output(args):
        _finish_result(result, json_output=True)
        return
    print(result.summary)
    for label in ("added", "removed", "changed", "breaking"):
        items = payload[label]
        if not items:
            continue
        print(f"\n{label.title()}:")
        for item in items:
            op = f"{item['method']} {item['path']}"
            if item.get("operation_id"):
                op = f"{op} ({item['operation_id']})"
            changes = item.get("changes")
            suffix = f" - {', '.join(changes)}" if changes else ""
            print(f"- {op}{suffix}")


def _run_chirp_app_check(args: argparse.Namespace) -> None:
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
    app.check(
        deploy=args.deploy,
        warnings_as_errors=args.warnings_as_errors or args.deploy,
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
    return sorted(errors, key=lambda finding: (finding.rule_id, finding.target, finding.message)), sorted(
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


def _run_check(args: argparse.Namespace) -> None:
    app_check_exit = 0
    if not args.content_only and not args.agent_only:
        _ensure_pythonpath()
        if _json_output(args) or args.report_format:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    _run_chirp_app_check(args)
                except SystemExit as exc:
                    app_check_exit = _coerce_exit_code(exc.code)
        else:
            _run_chirp_app_check(args)
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
    if app_check_exit:
        diagnostics.append(
            Diagnostic(
                severity="error",
                message="Chirp app contract check failed",
                rule_id="chirp.app_check",
                next_action="Run fura check without --json for the upstream Chirp check output.",
            )
        )
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
    has_error = bool(app_check_exit or errors or agent_errors)
    warnings_fail = bool((warnings or agent_warnings) and args.warnings_as_errors)
    exit_code = (
        ExitCode.VALIDATION_ERROR
        if has_error
        else ExitCode.WARNING
        if warnings_fail
        else ExitCode.SUCCESS
    )
    total_errors = len(errors) + len(agent_errors) + (1 if app_check_exit else 0)
    total_warnings = len(warnings) + len(agent_warnings)
    result = CommandResult(
        command=command_name(args),
        ok=exit_code == ExitCode.SUCCESS,
        exit_code=exit_code,
        summary=(f"check completed with {total_errors} error(s) and {total_warnings} warning(s)"),
        diagnostics=tuple(diagnostics),
        data={
            "error_count": total_errors,
            "warning_count": total_warnings,
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
    if _json_output(args):
        _finish_result(
            result,
            json_output=True,
        )
        return
    if args.report_format:
        from furatena.cli.reports import render_report

        print(render_report(result, args.report_format))
        if result.exit_code:
            raise SystemExit(int(result.exit_code))
        return
    for finding in agent_warnings:
        print(f"warning: {finding.message}")
    for finding in agent_errors:
        print(f"error: {finding.message}")
    for message in warnings:
        print(f"warning: {message}")
    for message in errors:
        print(f"error: {message}")
    if errors or agent_errors:
        raise SystemExit(1)
    if (warnings or agent_warnings) and args.warnings_as_errors:
        raise SystemExit(1)


def _run_impact(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.impact import stale_impact_report
    from furatena.catalog.lifecycle import check_stale_public_outputs
    from furatena.catalog.registry import CatalogRegistry

    app_root = _app_root(args)
    repo = _repo_for_app(app_root)
    config = load_docs_config(_docs_yaml(args))
    mounts_path = config.mounts_path or app_root / "mounts.yaml"
    registry = CatalogRegistry.from_config(
        mounts_path,
        repo_root=repo,
        app_root=app_root,
        rewrites_path=config.rewrites_path,
        inventories_path=config.inventories_path,
        catalog_identity=config.identity.to_meta(),
        autodoc=not args.no_autodoc,
        autodoc_config=_autodoc_config(args, repo),
    )
    frozen_dir = Path(args.frozen).expanduser().resolve() if args.frozen else app_root / "frozen"
    stale_public_outputs = tuple(dict.fromkeys(check_stale_public_outputs(registry, frozen_dir)))
    report = stale_impact_report(
        registry,
        slug=args.slug,
        stale_public_outputs=stale_public_outputs,
        include_private=args.include_private,
    )
    diagnostics = tuple(
        diagnostic_from_message(
            message,
            severity="warning",
            rule_id="fura.impact.stale_public_output",
            next_action="Refresh frozen public output with fura freeze or fura export --fresh.",
        )
        for message in stale_public_outputs
    )
    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary=f"impact report completed with {report['stale_count']} stale item(s)",
                diagnostics=diagnostics,
                data=report,
            ),
            json_output=True,
        )
        return
    if report["stale_count"] == 0:
        print("impact: no stale content detected")
        return
    print(f"impact: {report['stale_count']} stale item(s)")
    if report["task_markdown"]:
        print(report["task_markdown"])


def _run_mcp(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    sys.path.insert(0, str(_app_root(args)))
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.mcp import FuraMCPServer, MCPAccessPolicy, run_milo_stdio
    from furatena.catalog.runtime import ServeConfig, ServeMode

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    docs_yaml = _docs_yaml(args)
    if not docs_yaml.is_file():
        raise SystemExit(f"docs config not found: {docs_yaml}")

    frozen_dir = Path(args.frozen_dir).expanduser().resolve() if args.frozen_dir else app_root / "frozen"
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
        privileged_tokens=privileged_tokens,
        rate_limit_per_minute=args.rate_limit,
        timeout_seconds=args.timeout,
        max_output_chars=args.max_output_chars,
    )
    server = FuraMCPServer(
        docs,
        base_url=args.base_url or "",
        include_private=serve.mode == ServeMode.AUTHOR and args.include_private,
        policy=policy,
    )

    if args.describe:
        data = {
            "protocol_version": "2025-06-18",
            "transport": "milo-stdio",
            "policy": server.policy.to_dict(),
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


def _run_author(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    sys.path.insert(0, str(_app_root(args)))
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

    if command == "status":
        result = author_status(args.target, mounts=mounts, mount_id=mount_id)
    elif command == "validate":
        result = author_validate(args.target, mounts=mounts, mount_id=mount_id)
        if result.ok:
            errors, warnings = _run_docs_content_check(args=args)
            result = author_validate(
                args.target,
                mounts=mounts,
                mount_id=mount_id,
                validation_errors=tuple(errors),
                validation_warnings=tuple(warnings),
            )
    elif command == "new":
        result = author_new(
            args.slug,
            mounts=mounts,
            mount_id=mount_id,
            title=args.title,
            dry_run=args.dry_run,
            confirmed=args.yes,
        )
    elif command == "edit":
        result = author_apply_edit(
            args.target,
            mounts=mounts,
            mount_id=mount_id,
            old_text=args.old_text,
            new_text=args.new_text,
            dry_run=args.dry_run,
            confirmed=args.yes,
        )
    else:
        result = author_transition(
            command,
            args.target,
            mounts=mounts,
            mount_id=mount_id,
            dry_run=args.dry_run,
            confirmed=args.yes,
        )

    diagnostics = _author_diagnostics(result)
    exit_code = (
        ExitCode.SUCCESS
        if result.ok
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


def _run_migrate(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    sys.path.insert(0, str(_app_root(args)))
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.migrate import migrate_mdx_paths, migrate_mounts
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
                status = "active" if candidate.active else "exists" if candidate.exists else "missing"
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
                data={"files": payload, "copied": copied, "skipped": skipped, "force": bool(args.force)},
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


def _run_recipes(args: argparse.Namespace) -> None:
    from furatena.cli.recipes import all_recipes, get_recipe, recipe_ids

    selected = (get_recipe(args.recipe),) if args.recipe else all_recipes()
    if args.recipe and selected[0] is None:
        diagnostics = (
            Diagnostic(
                severity="error",
                message=f"unknown recipe: {args.recipe}",
                rule_id="fura.recipes",
                next_action=f"Choose one of: {', '.join(recipe_ids())}.",
            ),
        )
        result = CommandResult(
            command=command_name(args),
            ok=False,
            exit_code=ExitCode.CONFIG_ERROR,
            summary="unknown recipe",
            diagnostics=diagnostics,
            data={"available": recipe_ids()},
        )
        if _json_output(args):
            _finish_result(result, json_output=True)
        for diagnostic in diagnostics:
            print(f"error: {diagnostic.message}")
        raise SystemExit(int(ExitCode.CONFIG_ERROR))
    recipes = tuple(recipe for recipe in selected if recipe is not None)
    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary=f"returned {len(recipes)} recipe(s)",
                data={
                    "recipes": [recipe.to_dict() for recipe in recipes],
                    "count": len(recipes),
                    "available": recipe_ids(),
                },
            ),
            json_output=True,
        )
        return

    for index, recipe in enumerate(recipes):
        if index:
            print()
        print(f"{recipe.id}: {recipe.title}")
        print(f"  {recipe.summary}")
        print(f"  applies to: {', '.join(recipe.applies_to)}")
        print("  steps:")
        for step in recipe.steps:
            flags = []
            if step.dry_run:
                flags.append("dry-run")
            if step.requires_confirmation:
                flags.append("requires confirmation")
            suffix = f" ({', '.join(flags)})" if flags else ""
            print(f"    - {step.id}: {step.command}{suffix}")
            print(f"      {step.purpose}")
        if recipe.verifies:
            print(f"  verifies: {', '.join(recipe.verifies)}")


def _run_evals(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    sys.path.insert(0, str(_app_root(args)))
    from furatena.catalog.agent_evals import run_agent_evaluations
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.runtime import ServeConfig, ServeMode

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    docs = DocsApp.from_paths(
        _docs_yaml(args),
        repo_root=repo_root,
        autodoc_config=_autodoc_config(args, repo_root),
        autodoc=not args.no_autodoc,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
        workers=args.workers,
    )
    report = run_agent_evaluations(
        docs,
        include_private=args.include_private,
        categories=args.category,
    )
    fail_count = int(report["fail_count"])
    diagnostics = tuple(
        Diagnostic(
            severity="error",
            message=result["message"],
            source_path=result["id"],
            rule_id=f"fura.evals.{result['category']}",
            next_action="Inspect the observed and expected eval data, then fix retrieval or MCP metadata.",
        )
        for result in report["results"]
        if result["status"] == "fail"
    )
    exit_code = ExitCode.VALIDATION_ERROR if fail_count else ExitCode.SUCCESS
    result = CommandResult(
        command=command_name(args),
        ok=fail_count == 0,
        exit_code=exit_code,
        summary=(
            f"agent evals completed with {report['pass_count']} pass(es), "
            f"{report['fail_count']} failure(s), and {report['skip_count']} skip(s)"
        ),
        diagnostics=diagnostics,
        data=report,
    )
    if _json_output(args):
        _finish_result(result, json_output=True)
        return
    print(result.summary)
    for item in report["results"]:
        print(f"{item['status']}: {item['id']} - {item['message']}")
    if fail_count:
        raise SystemExit(int(exit_code))


def _run_init(args: argparse.Namespace) -> None:
    app_root = Path(args.directory).expanduser().resolve()
    force = args.force

    files = {
        "docs.yaml": dedent(
            f"""\
            shell: shell.html

            views:
              doc: views/doc.html
              doc_list: views/doc_list.html
              page: views/page.html
              home: views/home.html
              collection: views/collection.html
              api_reference: views/api_reference.html
              default: views/doc.html

            site:
              name: {args.name}
              tagline: Live documentation from markdown
              description: Write markdown. Get a fast, searchable docs site with static and agent exports.

            theme:
              use: lagoon
              id: furatena
              effects:
                code: flat
                cards: flat
                hero: wash

            mounts: mounts.yaml
            """
        ),
        "mounts.yaml": dedent(
            """\
            mounts:
              - id: docs
                label: Documentation
                content_root: content
                default: true
                extensions: [".md", ".mdx", ".html"]
                format_map:
                  ".md": patitas-markdown
                  ".mdx": mdx
                  ".html": html
            """
        ),
        "content/_index.md": dedent(
            f"""\
            ---
            title: {args.name}
            description: Live documentation from markdown.
            layout: home
            ---

            # {args.name}

            Start editing `content/docs/get-started.md`.
            """
        ),
        "content/docs/_index.md": dedent(
            """\
            ---
            title: Documentation
            description: Guides and reference.
            weight: 10
            ---

            # Documentation

            Browse the docs.
            """
        ),
        "content/docs/get-started.md": dedent(
            """\
            ---
            title: Get started
            description: Your first Furatena page.
            weight: 20
            ---

            # Get started

            Run the local docs server:

            ```bash
            fura serve
            ```
            """
        ),
        "theme/shell.html": dedent(
            """\
            {% extends "layouts/fura_shell.html" %}

            {% block title %}{% if node %}{{ node.title }}{% else %}{{ site_name | default('Furatena') }}{% end %}{% end %}

            {% block head %}
            {% for href in docs_stylesheets() %}
            <link rel="stylesheet" href="{{ href }}">
            {% end %}
            {% end %}

            {% block content %}
            {% block page_root %}{% end %}
            {% end %}
            """
        ),
        "theme/views/doc.html": dedent(
            """\
            {% extends "shell.html" %}

            {% block page_root %}
            <main id="page-root" class="chirp-theme-docs-layout" data-fura-surface="catalog">
            {% block page_root_inner %}
            {% block page_content %}
              <article class="chirp-theme-doc">
                <h1>{{ node.title }}</h1>
                {% if node.description %}<p>{{ node.description }}</p>{% end %}
                {% include "partials/author_chrome.html" %}
                {{ node | doc_body }}
              </article>
            {% end %}
            {% end %}
            </main>
            {% end %}

            {% block sse_scope %}
            {% include "partials/author_sse.html" %}
            {% end %}
            """
        ),
        "theme/views/doc_list.html": dedent(
            """\
            {% extends "views/doc.html" %}
            """
        ),
        "theme/views/changelog.html": dedent(
            """\
            {% extends "views/doc.html" %}
            """
        ),
        "theme/views/collection.html": dedent(
            """\
            {% extends "views/doc.html" %}
            """
        ),
        "theme/views/api_reference.html": dedent(
            """\
            {% extends "views/doc.html" %}
            """
        ),
        "theme/views/page.html": dedent(
            """\
            {% extends "views/doc.html" %}
            """
        ),
        "theme/views/home.html": dedent(
            """\
            {% extends "views/doc.html" %}
            """
        ),
        "theme/views/portal.html": dedent(
            """\
            {% extends "shell.html" %}

            {% block page_root %}
            <main id="page-root" class="chirp-theme-docs-layout" data-fura-surface="app">
              <h1>{{ site_name | default('Documentation') }}</h1>
              <ul>
                {% for mount in mounts %}
                <li><a href="{{ mount.href }}">{{ mount.label }}</a></li>
                {% end %}
              </ul>
            </main>
            {% end %}
            """
        ),
        "theme/views/author_studio.html": dedent(
            """\
            {% extends "shell.html" %}

            {% block page_root %}
            {% block author_studio_workspace %}
            <main id="author-studio-workspace"
                  class="chirp-theme-author-studio__workspace"
                  data-author-studio-mode="{{ author_studio.mode }}"
                  data-author-studio-ok="{{ 'true' if author_studio.ok else 'false' }}"
                  data-author-studio-provenance="{{ author_studio.source_provenance }}"
                  hx-disinherit="hx-select hx-target hx-swap">
              <header class="chirp-theme-author-studio__header">
                <div>
                  <p>{{ author_studio.visibility }}</p>
                  <h1>{{ author_studio.title }}</h1>
                  {% if author_studio.source_path %}<code>{{ author_studio.source_path }}</code>{% end %}
                </div>
                <button form="author-studio-form" type="submit">
                  {% if author_studio.mode == "create" %}Create draft{% else %}Save source{% end %}
                </button>
              </header>

              {% if author_studio.saved %}
              <p data-author-studio-saved="true">Saved</p>
              {% end %}

              {% if author_studio.diagnostics %}
              <section aria-label="Save diagnostics">
                {% for diagnostic in author_studio.diagnostics %}
                <article data-rule-id="{{ diagnostic.rule_id }}" data-severity="{{ diagnostic.severity }}">
                  <strong>{{ diagnostic.severity }}</strong>
                  <span>{{ diagnostic.message }}</span>
                  {% if diagnostic.next_action %}<small>{{ diagnostic.next_action }}</small>{% end %}
                </article>
                {% end %}
              </section>
              {% end %}

              <div class="chirp-theme-author-studio__split">
                <form id="author-studio-form"
                      method="post"
                      action="{{ author_studio.save_url }}"
                      hx-post="{{ author_studio.save_url }}"
                      hx-target="#author-studio-workspace"
                      hx-swap="outerHTML">
                  <input type="hidden" name="slug" value="{{ author_studio.slug }}">
                  <input type="hidden" name="mode" value="{{ author_studio.mode }}">
                  <input type="hidden" name="title" value="{{ author_studio.title }}">
                  <label for="author-studio-source">Source</label>
                  <textarea id="author-studio-source"
                            name="source"
                            spellcheck="false"
                            data-source-path="{{ author_studio.source_path }}"
                            data-has-patitas-ast="{{ 'true' if author_studio.has_ast else 'false' }}">{{ author_studio.source_text }}</textarea>
                </form>
                <section aria-label="Rendered preview">
                  {% if author_studio.preview_html %}
                  {{ author_studio.preview_html | safe }}
                  {% else %}
                  <h1>{{ author_studio.title }}</h1>
                  {% end %}
                </section>
              </div>

              {% if author_studio.source_regions %}
              <ol aria-label="Source regions">
                {% for region in author_studio.source_regions %}
                <li data-source-line="{{ region.line }}"
                    data-heading-depth="{{ region.depth }}"
                    data-preview-anchor="{{ region.anchor }}">
                  <span>{{ region.heading }}</span>
                  <code>L{{ region.line }}</code>
                </li>
                {% end %}
              </ol>
              {% end %}
            </main>
            {% end %}
            {% end %}
            """
        ),
        "theme/views/develop.html": dedent(
            """\
            {% extends "shell.html" %}

            {% block page_root %}
            <main id="page-root" class="chirp-theme-docs-layout" data-fura-surface="app">
              <h1>Develop</h1>
              <ul>
                {% for item in develop_exports %}
                <li><a href="{{ item.preview_href }}">{{ item.label }}</a></li>
                {% end %}
              </ul>
            </main>
            {% end %}
            """
        ),
        "theme/views/develop_export.html": dedent(
            """\
            {% extends "shell.html" %}

            {% block page_root %}
            <main id="page-root" class="chirp-theme-docs-layout" data-fura-surface="app">
              <h1>{{ develop_export.label }}</h1>
              <pre><code>{{ develop_sample }}</code></pre>
            </main>
            {% end %}
            """
        ),
        "theme/search.html": dedent(
            """\
            {% extends "shell.html" %}

            {% block page_root %}
            <main id="page-root" class="chirp-theme-docs-layout" data-fura-surface="catalog">
              <h1>Search</h1>
              {% block search_results %}
              {% include "partials/search_results.html" %}
              {% end %}
            </main>
            {% end %}
            """
        ),
        "theme/assets/branding/favicon.svg": dedent(
            """\
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
              <rect width="64" height="64" rx="12" fill="#111827"/>
              <path d="M18 44V20h30v6H26v6h18v6H26v6z" fill="#f8fafc"/>
            </svg>
            """
        ),
        "theme/assets/branding/site.webmanifest": dedent(
            f"""\
            {{"name":"{args.name}","short_name":"{args.name}","icons":[],"theme_color":"#111827","background_color":"#ffffff","display":"standalone"}}
            """
        ),
    }

    written: list[Path] = []
    for rel, body in files.items():
        target = app_root / rel
        if target.exists() and not force:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        written.append(target)

    if not written:
        if _json_output(args):
            _finish_result(
                CommandResult(
                    command=command_name(args),
                    ok=True,
                    summary="no scaffold files written",
                    data={
                        "app_root": app_root,
                        "written": [],
                        "skipped_existing": True,
                        "force": bool(force),
                    },
                ),
                json_output=True,
            )
            return
        print(f"no files written — {app_root} already has a Furatena scaffold (use --force)")
        return
    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary=f"initialized Furatena app at {app_root}",
                data={
                    "app_root": app_root,
                    "written": [path.relative_to(app_root) for path in written],
                    "count": len(written),
                    "force": bool(force),
                },
            ),
            json_output=True,
        )
        return
    print(f"initialized Furatena app at {app_root}")
    for path in written:
        print(f"  {path.relative_to(app_root)}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fura",
        description="Fura — CLI for Furatena (hypermedia docs catalog)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Furatena {__import__('furatena').__version__}",
    )
    parser.add_argument(
        "--app-root",
        default=None,
        help="Furatena app root containing docs.yaml (default: cwd, ./app, or packaged dogfood app)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to docs.yaml (default: APP_ROOT/docs.yaml)",
    )
    parser.add_argument(
        "--autodoc-config",
        default=None,
        help="Path to autodoc.yaml (default: REPO/config/autodoc.yaml when present)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Scaffold a standalone Furatena app")
    init.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Target app directory (default current directory)",
    )
    init.add_argument("--name", default="Furatena Docs", help="Site name")
    init.add_argument("--force", action="store_true", help="Overwrite scaffold files")
    init.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    init.set_defaults(handler=_run_init)

    serve = sub.add_parser("serve", help="Run the Furatena app")
    serve.add_argument("--host", default=None, help="Bind host (default 127.0.0.1)")
    serve.add_argument("--port", type=int, default=None, help="Bind port (default 8001)")
    mode = serve.add_mutually_exclusive_group()
    mode.add_argument("--author", action="store_true", help="Live index from source")
    mode.add_argument("--preview", action="store_true", help="Frozen catalog only")
    mode.add_argument("--hybrid", action="store_true", help="Frozen baseline + live overlay")
    serve.add_argument("--frozen", action="store_true", help="Alias for --preview")
    serve.add_argument("--no-autodoc", action="store_true", help="Skip autodoc slice")
    serve.add_argument("--channel", default=None, help="Version channel (FURA_CHANNEL)")
    serve.add_argument("--base-url", default=None, help="Public origin (FURA_BASE_URL)")
    serve.add_argument("--workers", type=int, default=None, help="Parallel index workers")
    serve.add_argument("--json", action="store_true", help="Emit startup as standard command result JSON")
    serve.set_defaults(handler=_run_serve)

    stop = sub.add_parser("stop", help="Stop a stray Furatena dev server for this workspace")
    stop.add_argument("--host", default=None, help="Bind host (default 127.0.0.1)")
    stop.add_argument("--port", type=int, default=None, help="Bind port (default 8001)")
    stop.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    stop.set_defaults(handler=_run_stop)

    freeze = sub.add_parser("freeze", help="Export catalog JSON + HTML fragments")
    freeze.add_argument("--full", action="store_true", help="Force full rebuild")
    freeze.add_argument("--workers", type=int, default=None, help="Parallel workers")
    freeze.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    freeze.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Output directory (default app/frozen)",
    )
    freeze.set_defaults(handler=_run_freeze)

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

    pdf = sub.add_parser("pdf", help="Export catalog pages to PDF artifacts")
    pdf_scope = pdf.add_mutually_exclusive_group()
    pdf_scope.add_argument("--page", default=None, help="Page URL or slug to export")
    pdf_scope.add_argument("--collection", default=None, help="Collection, section, or mount to export")
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

    query = sub.add_parser("query", help="Query catalog content IR")
    query.add_argument("--directive", default=None)
    query.add_argument("--heading", default=None)
    query.add_argument("--mount", default=None)
    query.add_argument("--edition", default=None)
    query.add_argument("--tag", default=None)
    query.add_argument("--url-prefix", default=None)
    query.add_argument("--json", action="store_true")
    query.add_argument("--no-autodoc", action="store_true")
    query.set_defaults(handler=_run_query)

    check = sub.add_parser("check", help="Run Chirp contract + content checks")
    check.add_argument("app", nargs="?", default=None, help="Optional app import string")
    check.add_argument("--content-only", action="store_true")
    check.add_argument("--agent", action="store_true", help="Include agent MCP/resource contract lint")
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

    api_diff = sub.add_parser("api-diff", help="Compare two OpenAPI specs for operation changes")
    api_diff.add_argument("old", help="Old OpenAPI YAML/JSON spec")
    api_diff.add_argument("new", help="New OpenAPI YAML/JSON spec")
    api_diff.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    api_diff.set_defaults(handler=_run_api_diff)

    impact = sub.add_parser("impact", help="Report stale content impact for agents and CI")
    impact.add_argument("--slug", default=None, help="Optional slug used to scope stale-impact entries")
    impact.add_argument("--frozen", default=None, help="Frozen catalog directory (default app/frozen)")
    impact.add_argument("--include-private", action="store_true", help="Include private graph context")
    impact.add_argument("--no-autodoc", action="store_true", help="Skip autodoc slice")
    impact.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    impact.set_defaults(handler=_run_impact)

    recipes = sub.add_parser("recipes", help="Show stable agent workflow recipes")
    recipes.add_argument(
        "recipe",
        nargs="?",
        default=None,
        help="Optional recipe id, e.g. init, inspect, validate, query, publish, repair, source-sync",
    )
    recipes.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    recipes.set_defaults(handler=_run_recipes)

    evals = sub.add_parser("evals", help="Run deterministic agent retrieval and tool-selection evals")
    evals.add_argument(
        "--category",
        action="append",
        default=[],
        help="Run one eval category or case id; may be repeated",
    )
    evals.add_argument("--include-private", action="store_true", help="Exercise include-private author MCP evals")
    evals.add_argument("--no-autodoc", action="store_true", help="Skip autodoc slice")
    evals.add_argument("--workers", type=int, default=None, help="Parallel index workers")
    evals.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    evals.set_defaults(handler=_run_evals)

    mcp = sub.add_parser("mcp", help="Run a local MCP server over stdio")
    mcp_mode = mcp.add_mutually_exclusive_group()
    mcp_mode.add_argument("--author", action="store_true", help="Serve live source catalog data")
    mcp_mode.add_argument("--preview", action="store_true", help="Serve frozen catalog data")
    mcp_mode.add_argument("--hybrid", action="store_true", help="Serve frozen baseline with live overlay")
    mcp.add_argument("--frozen-dir", default=None, help="Frozen catalog directory (default app/frozen)")
    mcp.add_argument("--no-autodoc", action="store_true", help="Skip autodoc slice")
    mcp.add_argument("--base-url", default="", help="Public origin for absolute search URLs")
    mcp.add_argument("--workers", type=int, default=None, help="Parallel index workers")
    mcp.add_argument(
        "--include-private",
        action="store_true",
        help="In author mode, expose draft/private nodes through MCP resources and tools",
    )
    mcp.add_argument("--remote", action="store_true", help="Apply remote MCP auth, audit, and safety policy")
    mcp.add_argument("--actor", default="", help="Actor id recorded in MCP audit events")
    mcp.add_argument("--tenant", default=None, help="Tenant id recorded in MCP audit events")
    mcp.add_argument("--site", default=None, help="Site id recorded in MCP audit events")
    mcp.add_argument(
        "--privileged-token",
        default="",
        help="Token required by remote MCP clients before sensitive authoring tools can run",
    )
    mcp.add_argument(
        "--rate-limit",
        type=int,
        default=120,
        help="Maximum MCP tool calls per minute for this server session",
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
    mcp.add_argument("--describe", action="store_true", help="Describe MCP resources/tools and exit")
    mcp.add_argument("--json", action="store_true", help="With --describe, emit the standard command result JSON")
    mcp.set_defaults(handler=_run_mcp)

    author = sub.add_parser("author", help="Local author lifecycle operations")
    author_sub = author.add_subparsers(dest="author_command", required=True)

    author_new_cmd = author_sub.add_parser("new", help="Create a draft page")
    author_new_cmd.add_argument("slug", help="Page slug under the selected mount, e.g. docs/new-page")
    author_new_cmd.add_argument("--title", default=None, help="Page title (default from slug)")
    author_new_cmd.add_argument("--mount", default=None, help="Mount id from mounts.yaml")
    author_new_cmd.add_argument("--dry-run", action="store_true", help="Preview without writing")
    author_new_cmd.add_argument("--yes", action="store_true", help="Confirm source mutation")
    author_new_cmd.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    author_new_cmd.set_defaults(handler=_run_author)

    author_status_cmd = author_sub.add_parser("status", help="Inspect lifecycle status for a page")
    author_status_cmd.add_argument("target", help="Source path or page slug")
    author_status_cmd.add_argument("--mount", default=None, help="Mount id from mounts.yaml")
    author_status_cmd.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    author_status_cmd.set_defaults(handler=_run_author)

    author_validate_cmd = author_sub.add_parser("validate", help="Validate a source page")
    author_validate_cmd.add_argument("target", help="Source path or page slug")
    author_validate_cmd.add_argument("--mount", default=None, help="Mount id from mounts.yaml")
    author_validate_cmd.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    author_validate_cmd.set_defaults(handler=_run_author)

    author_edit_cmd = author_sub.add_parser("edit", help="Apply an exact-text edit to a source page")
    author_edit_cmd.add_argument("target", help="Source path or page slug")
    author_edit_cmd.add_argument("--old-text", required=True, help="Exact source span to replace")
    author_edit_cmd.add_argument("--new-text", required=True, help="Replacement source text")
    author_edit_cmd.add_argument("--mount", default=None, help="Mount id from mounts.yaml")
    author_edit_cmd.add_argument("--dry-run", action="store_true", help="Preview without writing")
    author_edit_cmd.add_argument("--yes", action="store_true", help="Confirm source mutation")
    author_edit_cmd.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
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
        item.add_argument("--dry-run", action="store_true", help="Preview without writing")
        item.add_argument("--yes", action="store_true", help="Confirm source mutation")
        item.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
        item.set_defaults(handler=_run_author)

    theme = sub.add_parser("theme", help="List installable theme packs")
    theme_sub = theme.add_subparsers(dest="theme_command", required=True)
    theme_list = theme_sub.add_parser("list", help="Show furatena.themes entry points")
    theme_list.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    theme_list.set_defaults(handler=_run_theme_list)
    theme_inspect = theme_sub.add_parser("inspect", help="Show resolved theme templates/assets")
    theme_inspect.add_argument("path", nargs="?", default=None, help="Logical path, e.g. views/doc.html")
    theme_inspect.add_argument("--verbose", action="store_true", help="Show full resolution chain")
    theme_inspect.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    theme_inspect.set_defaults(handler=_run_theme_inspect)
    theme_eject = theme_sub.add_parser("eject", help="Copy a resolved theme file into local overrides")
    theme_eject.add_argument("path", nargs="?", default=None, help="Logical path, e.g. views/doc.html")
    theme_eject.add_argument("--all", action="store_true", help="Eject every inspectable template/asset")
    theme_eject.add_argument("--force", action="store_true", help="Overwrite existing local files")
    theme_eject.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    theme_eject.set_defaults(handler=_run_theme_eject)
    theme_diff = theme_sub.add_parser("diff", help="Diff a local override against upstream")
    theme_diff.add_argument("path", help="Logical path, e.g. views/doc.html")
    theme_diff.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    theme_diff.set_defaults(handler=_run_theme_diff)
    theme_init = theme_sub.add_parser("init", help="Scaffold a project skin pack directory")
    theme_init.add_argument(
        "directory",
        nargs="?",
        default=str(_app_root() / "theme-skin"),
        help="Output directory (default app/theme-skin)",
    )
    theme_init.add_argument("--force", action="store_true", help="Overwrite existing scaffold files")
    theme_init.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    theme_init.set_defaults(handler=_run_theme_init)

    migrate = sub.add_parser("migrate", help="Lower MDX JSX to Patitas directives")
    migrate.add_argument("paths", nargs="*", help="Optional .mdx files")
    migrate.add_argument(
        "--report",
        action="store_true",
        help="Report migration readiness risks without writing files",
    )
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("--keep-mdx", action="store_true")
    migrate.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    migrate.set_defaults(handler=_run_migrate)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()
