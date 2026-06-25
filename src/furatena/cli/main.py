"""Fura — CLI for Furatena."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _app_root() -> Path:
    return _repo_root() / "app"


def _ensure_pythonpath() -> None:
    repo = _repo_root()
    src = str(repo / "src")
    app = str(_app_root())
    existing = os.environ.get("PYTHONPATH", "")
    parts = [p for p in (src, app, existing) if p]
    os.environ["PYTHONPATH"] = os.pathsep.join(parts)


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

    sys.path.insert(0, str(_app_root()))
    import app as docs_app  # noqa: E402

    port = args.port or int(os.environ.get("FURA_PORT", "8001"))
    host = args.host or "127.0.0.1"
    url = f"http://{host}:{port}/"
    from furatena.catalog.dev_banner import format_serve_startup

    for line in format_serve_startup(
        docs_app._docs.serve,
        page_count=len(docs_app.catalog.nodes),
        mount_count=len(docs_app.catalog.mounts),
        url=url,
    ):
        print(line)
    docs_app.app.run(port=port, host=host)


def _run_freeze(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    if args.workers is not None:
        os.environ["FURA_WORKERS"] = str(args.workers)
    sys.path.insert(0, str(_app_root()))
    import freeze_catalog  # noqa: E402

    sys.argv = ["freeze_catalog.py"]
    if args.full:
        sys.argv.append("--full")
    if args.workers is not None:
        sys.argv.extend(["--workers", str(args.workers)])
    sys.argv.append(str(Path(args.output)))
    freeze_catalog.main()


def _run_export(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    if args.base_url:
        os.environ["FURA_BASE_URL"] = args.base_url
    if args.base_path:
        os.environ["FURA_BASE_PATH"] = args.base_path
    sys.path.insert(0, str(_app_root()))
    import export_catalog  # noqa: E402

    argv = ["export_catalog.py", str(Path(args.output))]
    if args.frozen:
        argv.extend(["--frozen", str(Path(args.frozen))])
    if args.base_path:
        argv.extend(["--base-path", args.base_path])
    if args.base_url:
        argv.extend(["--site-url", args.base_url])
    if args.no_index_txt:
        argv.append("--no-index-txt")
    if args.incremental:
        argv.append("--incremental")
    if args.fresh:
        argv.append("--fresh")
    export_catalog.main(argv[1:])


def _run_query(args: argparse.Namespace) -> None:
    import json

    _ensure_pythonpath()
    sys.path.insert(0, str(_app_root()))
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.query import query_catalog
    from furatena.catalog.registry import CatalogRegistry

    repo = _repo_root()
    app_root = _app_root()
    config = load_docs_config(app_root / "docs.yaml")
    mounts_path = config.mounts_path or app_root / "mounts.yaml"
    autodoc_config = repo / "config" / "autodoc.yaml"
    registry = CatalogRegistry.from_config(
        mounts_path,
        repo_root=repo,
        app_root=app_root,
        rewrites_path=config.rewrites_path,
        inventories_path=config.inventories_path,
        autodoc=not args.no_autodoc,
        autodoc_config=autodoc_config if autodoc_config.is_file() else None,
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
        print(json.dumps(results, indent=2))
    else:
        for row in results:
            print(f"{row['url']}\t{row['title']}")
    if not results:
        raise SystemExit(1)


def _run_docs_content_check(
    *,
    warnings_as_errors: bool = False,
    strict_views: bool = False,
    strict_edition_links: bool = False,
) -> None:
    from furatena.catalog.check import check_catalog
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.registry import CatalogRegistry
    from furatena.catalog.theme import DocsTheme
    from furatena.catalog.views import ViewRegistry

    repo = _repo_root()
    app_root = _app_root()
    config = load_docs_config(app_root / "docs.yaml")
    theme = DocsTheme.from_docs_config(config)
    mounts_path = config.mounts_path or app_root / "mounts.yaml"
    autodoc_config = repo / "config" / "autodoc.yaml"
    registry = CatalogRegistry.from_config(
        mounts_path,
        repo_root=repo,
        app_root=app_root,
        rewrites_path=config.rewrites_path,
        inventories_path=config.inventories_path,
        autodoc=False,
        autodoc_config=autodoc_config if autodoc_config.is_file() else None,
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
    for message in warnings:
        print(f"warning: {message}")
    for message in errors:
        print(f"error: {message}")
    if errors:
        raise SystemExit(1)
    if warnings and warnings_as_errors:
        raise SystemExit(1)


def _run_check(args: argparse.Namespace) -> None:
    if not args.content_only:
        from chirp.cli._resolve import resolve_app

        _ensure_pythonpath()
        sys.path.insert(0, str(_app_root()))
        import_string = args.app or "app:app"
        app = resolve_app(import_string)
        app.check(
            deploy=args.deploy,
            warnings_as_errors=args.warnings_as_errors or args.deploy,
        )
    else:
        _ensure_pythonpath()
        sys.path.insert(0, str(_app_root()))
    _run_docs_content_check(
        warnings_as_errors=args.warnings_as_errors,
        strict_views=args.deploy or args.warnings_as_errors,
        strict_edition_links=args.strict_edition_links or args.deploy,
    )


def _run_migrate(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    sys.path.insert(0, str(_app_root()))
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.migrate import migrate_mdx_paths, migrate_mounts
    from furatena.catalog.registry import load_mounts

    repo = _repo_root()
    app_root = _app_root()
    config = load_docs_config(app_root / "docs.yaml")
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
        print("No MDX files found.")
        return

    errors = 0
    for report in reports:
        if report.errors:
            errors += 1
            for message in report.errors:
                print(f"error: {report.source_path}: {message}")
            continue
        if not report.changed and not report.written:
            print(f"skip {report.source_path} (already canonical)")
            continue
        status = "would write" if not write else "migrated"
        print(f"{status} {report.source_path} -> {report.target_path}")
        for component in report.unmigrated_components:
            print(f"warning: {report.source_path}: unmigrated JSX component {component}")
        for message in report.warnings:
            print(f"warning: {report.source_path}: {message}")

    if errors:
        raise SystemExit(1)


def _run_theme_list(_args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    from furatena.catalog.docs_core import list_docs_core_ids, load_docs_core
    from furatena.catalog.theme_pack import list_theme_packs, load_theme_pack

    print("docs-core (theme.id):")
    for theme_id in list_docs_core_ids():
        pack = load_docs_core(theme_id)
        if pack is not None:
            print(f"  {theme_id}\t{pack.root}")
    print("skin packs (theme.use):")
    names = list_theme_packs()
    if not names:
        print("  (none registered)")
        return
    for name in names:
        pack = load_theme_pack(name)
        print(f"  {name}\t{pack.root}")


def _run_theme_init(args: argparse.Namespace) -> None:
    from furatena.catalog.theme_init import init_theme_pack

    target = Path(args.directory)
    written = init_theme_pack(target, force=args.force)
    if not written:
        print(f"no files written — {target} already initialized (use --force to overwrite)")
        return
    print(f"initialized skin scaffold at {target} ({len(written)} files)")
    for path in written:
        print(f"  {path.relative_to(target)}")


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
    sub = parser.add_subparsers(dest="command", required=True)

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
    serve.set_defaults(handler=_run_serve)

    freeze = sub.add_parser("freeze", help="Export catalog JSON + HTML fragments")
    freeze.add_argument("--full", action="store_true", help="Force full rebuild")
    freeze.add_argument("--workers", type=int, default=None, help="Parallel workers")
    freeze.add_argument(
        "output",
        nargs="?",
        default=str(_app_root() / "frozen"),
        help="Output directory (default app/frozen)",
    )
    freeze.set_defaults(handler=_run_freeze)

    export = sub.add_parser("export", help="Link frozen catalog into static HTML")
    export.add_argument(
        "output",
        nargs="?",
        default=str(_app_root() / "public"),
        help="Output directory (default app/public)",
    )
    export.add_argument(
        "--frozen",
        default=str(_app_root() / "frozen"),
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
    export.set_defaults(handler=_run_export)

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
    check.add_argument("app", nargs="?", default="app:app", help="App import string")
    check.add_argument("--content-only", action="store_true")
    check.add_argument("--warnings-as-errors", action="store_true")
    check.add_argument("--deploy", action="store_true")
    check.add_argument("--strict-edition-links", action="store_true")
    check.set_defaults(handler=_run_check)

    theme = sub.add_parser("theme", help="List installable theme packs")
    theme_sub = theme.add_subparsers(dest="theme_command", required=True)
    theme_list = theme_sub.add_parser("list", help="Show furatena.themes entry points")
    theme_list.set_defaults(handler=_run_theme_list)
    theme_init = theme_sub.add_parser("init", help="Scaffold a project skin pack directory")
    theme_init.add_argument(
        "directory",
        nargs="?",
        default=str(_app_root() / "theme-skin"),
        help="Output directory (default app/theme-skin)",
    )
    theme_init.add_argument("--force", action="store_true", help="Overwrite existing scaffold files")
    theme_init.set_defaults(handler=_run_theme_init)

    migrate = sub.add_parser("migrate", help="Lower MDX JSX to Patitas directives")
    migrate.add_argument("paths", nargs="*", help="Optional .mdx files")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("--keep-mdx", action="store_true")
    migrate.set_defaults(handler=_run_migrate)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()
