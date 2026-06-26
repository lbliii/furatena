"""Fura — CLI for Furatena."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from textwrap import dedent


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
        print(line)
    docs.run_serve(port=port, host=host)


def _run_stop(args: argparse.Namespace) -> None:
    from furatena.catalog.dev_reload import stop_dev_server

    host = args.host or "127.0.0.1"
    port = args.port or int(os.environ.get("FURA_PORT", "8001"))
    if stop_dev_server(_repo_root(), host=host, port=port):
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
    )
    result = export_static_site(docs, options)
    base = options.base_path or "/"
    print(
        f"Exported {result.page_count} pages + {result.sidecar_count} sidecars "
        f"to {result.output_dir} (base_path={base}, skipped={result.skipped_count})"
    )


def _run_query(args: argparse.Namespace) -> None:
    import json

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
        print(json.dumps(results, indent=2))
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
) -> None:
    from furatena.catalog.check import check_catalog
    from furatena.catalog.config import load_docs_config
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
        _ensure_pythonpath()
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
    else:
        _ensure_pythonpath()
        sys.path.insert(0, str(_app_root(args)))
    _run_docs_content_check(
        args=args,
        warnings_as_errors=args.warnings_as_errors,
        strict_views=args.deploy or args.warnings_as_errors,
        strict_edition_links=args.strict_edition_links or args.deploy,
    )


def _run_migrate(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    sys.path.insert(0, str(_app_root(args)))
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.migrate import migrate_mdx_paths, migrate_mounts
    from furatena.catalog.registry import load_mounts

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
    for path in paths:
        result = eject_theme_path(docs, path, force=args.force)
        copied += result.copied_files
        if result.skipped:
            skipped += 1
            print(f"skip {result.logical_path} (already local at {result.target_path})")
            continue
        print(f"ejected {result.logical_path}")
        print(f"  from {result.source_path}")
        print(f"  to   {result.target_path}")
    print(f"wrote {copied} file(s), skipped {skipped}")


def _run_theme_diff(args: argparse.Namespace) -> None:
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.theme_customization import diff_theme_path

    docs = load_docs_config(_docs_yaml(args))
    diff = diff_theme_path(docs, args.path)
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
        print(f"no files written — {target} already initialized (use --force to overwrite)")
        return
    print(f"initialized skin scaffold at {target} ({len(written)} files)")
    for path in written:
        print(f"  {path.relative_to(target)}")


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
              <article class="chirp-theme-doc">
                <h1>{{ node.title }}</h1>
                {% if node.description %}<p>{{ node.description }}</p>{% end %}
                {{ node | doc_body }}
              </article>
            </main>
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
        print(f"no files written — {app_root} already has a Furatena scaffold (use --force)")
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
    serve.set_defaults(handler=_run_serve)

    stop = sub.add_parser("stop", help="Stop a stray Furatena dev server for this workspace")
    stop.add_argument("--host", default=None, help="Bind host (default 127.0.0.1)")
    stop.add_argument("--port", type=int, default=None, help="Bind port (default 8001)")
    stop.set_defaults(handler=_run_stop)

    freeze = sub.add_parser("freeze", help="Export catalog JSON + HTML fragments")
    freeze.add_argument("--full", action="store_true", help="Force full rebuild")
    freeze.add_argument("--workers", type=int, default=None, help="Parallel workers")
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
    check.add_argument("app", nargs="?", default=None, help="Optional app import string")
    check.add_argument("--content-only", action="store_true")
    check.add_argument("--warnings-as-errors", action="store_true")
    check.add_argument("--deploy", action="store_true")
    check.add_argument("--strict-edition-links", action="store_true")
    check.set_defaults(handler=_run_check)

    theme = sub.add_parser("theme", help="List installable theme packs")
    theme_sub = theme.add_subparsers(dest="theme_command", required=True)
    theme_list = theme_sub.add_parser("list", help="Show furatena.themes entry points")
    theme_list.set_defaults(handler=_run_theme_list)
    theme_inspect = theme_sub.add_parser("inspect", help="Show resolved theme templates/assets")
    theme_inspect.add_argument("path", nargs="?", default=None, help="Logical path, e.g. views/doc.html")
    theme_inspect.add_argument("--verbose", action="store_true", help="Show full resolution chain")
    theme_inspect.set_defaults(handler=_run_theme_inspect)
    theme_eject = theme_sub.add_parser("eject", help="Copy a resolved theme file into local overrides")
    theme_eject.add_argument("path", nargs="?", default=None, help="Logical path, e.g. views/doc.html")
    theme_eject.add_argument("--all", action="store_true", help="Eject every inspectable template/asset")
    theme_eject.add_argument("--force", action="store_true", help="Overwrite existing local files")
    theme_eject.set_defaults(handler=_run_theme_eject)
    theme_diff = theme_sub.add_parser("diff", help="Diff a local override against upstream")
    theme_diff.add_argument("path", help="Logical path, e.g. views/doc.html")
    theme_diff.set_defaults(handler=_run_theme_diff)
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
