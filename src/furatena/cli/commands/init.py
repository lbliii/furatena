"""init command parser and execution."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from textwrap import dedent
from typing import Any

from furatena import __version__
from furatena.cli.commands._shared import CommandModule
from furatena.cli.contracts import CommandResult, command_name

STARTERS = ("minimal", "api-portal", "multi-mount")


def _repository_files(starter: str, name: str) -> dict[str, str]:
    project_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "furatena"
    audiences = {
        "minimal": "A small repo-owned documentation site with the shortest path to static output.",
        "api-portal": "A DevRel portal combining authored guides with an OpenAPI reference.",
        "multi-mount": "A platform documentation hub with independently mounted product, SDK, and operations sources.",
    }
    first_edits = {
        "minimal": "content/docs/get-started.md",
        "api-portal": "content/docs/get-started.md and specs/openapi.yaml",
        "multi-mount": "content/product/docs/get-started.md, content/sdk/docs/sdk-quickstart.md, or content/operations/docs/runbook.md",
    }
    return {
        "pyproject.toml": dedent(
            f"""\
            [project]
            name = {json.dumps(f"{project_slug}-docs")}
            version = "0.1.0"
            requires-python = ">=3.14,<3.15"
            dependencies = ["furatena=={__version__}"]

            [tool.uv]
            python-preference = "only-managed"
            """
        ),
        ".gitignore": dedent(
            """\
            .venv/
            .docs-cache/
            frozen/
            public/
            """
        ),
        ".github/workflows/docs.yml": dedent(
            """\
            name: docs

            on:
              push:
              pull_request:

            permissions:
              contents: read

            jobs:
              validate-and-export:
                runs-on: ubuntu-latest
                env:
                  PYTHON_GIL: "0"
                steps:
                  - uses: actions/checkout@v4
                  - uses: astral-sh/setup-uv@v8.2.0
                  - run: uv python install 3.14t
                  - run: uv sync
                  - name: Prove free-threading
                    run: uv run python -c 'import sys; assert not sys._is_gil_enabled()'
                  - run: uv run fura check --content-only --warnings-as-errors
                  - run: uv run fura freeze
                  - run: uv run fura export --base-path ""
                  - uses: actions/upload-pages-artifact@v3
                    with:
                      path: public
            """
        ),
        "README.md": dedent(
            f"""\
            # {name}

            Furatena `{starter}` starter pinned to Furatena `{__version__}`.

            **Audience:** {audiences[starter]}

            ## From clone to export

            ```bash
            uv python install 3.14t
            uv sync
            PYTHON_GIL=0 uv run fura check --content-only --warnings-as-errors
            PYTHON_GIL=0 uv run fura freeze
            PYTHON_GIL=0 uv run fura export --base-path ""
            ```

            Edit `{first_edits[starter]}` first. The deployable site is written to
            `public/`; `frozen/` contains the portable catalog and agent sidecars.
            """
        ),
    }


def _api_portal_files(name: str) -> dict[str, str]:
    return {
        "config/autodoc.yaml": dedent(
            """\
            autodoc:
              python:
                enabled: false
              openapi:
                enabled: true
                output_prefix: api/rest
                display_name: REST API
                specs:
                  - specs/openapi.yaml
            """
        ),
        "specs/openapi.yaml": dedent(
            f"""\
            openapi: 3.1.0
            info:
              title: {json.dumps(f"{name} API")}
              version: 1.0.0
              description: Stable example API for the generated Furatena portal.
            servers:
              - url: https://api.example.com
                description: Production
            paths:
              /widgets:
                get:
                  operationId: listWidgets
                  summary: List widgets
                  description: Return the widgets visible to the current API consumer.
                  tags: [Widgets]
                  responses:
                    '200':
                      description: Widget collection
                      content:
                        application/json:
                          schema:
                            type: array
                            items:
                              $ref: '#/components/schemas/Widget'
                          examples:
                            sample:
                              value:
                                - id: widget-1
                                  name: Example widget
            components:
              schemas:
                Widget:
                  type: object
                  required: [id, name]
                  properties:
                    id:
                      type: string
                    name:
                      type: string
            """
        ),
        "content/docs/get-started.md": dedent(
            """\
            ---
            title: Start with the API portal
            description: Edit a guide and an OpenAPI operation from one repository.
            weight: 20
            ---

            # Start with the API portal

            Edit this guide, then edit `specs/openapi.yaml`. Furatena projects the
            OpenAPI operations into `/api/rest/`, search, static output, and agent
            sidecars from the same source contract.
            """
        ),
    }


def _multi_mount_files() -> dict[str, str]:
    return {
        "mounts.yaml": dedent(
            """\
            mounts:
              - id: product
                label: Product
                content_root: content/product
                default: true
                extensions: [".md", ".mdx", ".html"]
              - id: sdk
                label: SDK
                content_root: content/sdk
                url_prefix: /sdk
                extensions: [".md", ".rst", ".myst"]
              - id: operations
                label: Operations
                content_root: content/operations
                url_prefix: /operations
                extensions: [".md", ".html"]
            """
        ),
        "content/product/_index.md": dedent(
            """\
            ---
            title: Product documentation
            description: Product guides from the default mount.
            layout: home
            ---

            # Product documentation

            Start with the product guide, then follow the SDK and operations mounts.
            """
        ),
        "content/product/docs/_index.md": dedent(
            """\
            ---
            title: Product guides
            description: Adopt and use the product.
            ---

            # Product guides
            """
        ),
        "content/product/docs/get-started.md": dedent(
            """\
            ---
            title: Product quickstart
            description: First successful product workflow.
            ---

            # Product quickstart

            Continue with the [SDK quickstart](/sdk/docs/sdk-quickstart/) or the
            [operations runbook](/operations/docs/runbook/).
            """
        ),
        "content/sdk/_index.md": dedent(
            """\
            ---
            title: SDK documentation
            description: SDK guides from an independent mount.
            ---

            # SDK documentation
            """
        ),
        "content/sdk/docs/sdk-quickstart.md": dedent(
            """\
            ---
            title: SDK quickstart
            description: Install and call the SDK.
            ---

            # SDK quickstart

            This page is owned by the `sdk` mount and publishes below `/sdk/`.
            """
        ),
        "content/operations/_index.md": dedent(
            """\
            ---
            title: Operations
            description: Operator guidance from an independent mount.
            ---

            # Operations
            """
        ),
        "content/operations/docs/runbook.md": dedent(
            """\
            ---
            title: Service runbook
            description: Verify and recover the documentation service.
            ---

            # Service runbook

            Run `fura check`, rebuild `frozen/`, and promote `public/` only after
            validation succeeds.
            """
        ),
    }


def _run_init(args: argparse.Namespace) -> CommandResult:
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
            {% if fura_form_proof() %}
            <meta name="csrf-token" content="{{ fura_form_proof() }}">
            {% end %}
            {% for href in docs_stylesheets() %}
            <link rel="stylesheet" href="{{ href }}">
            {% end %}
            {% include "partials/head_meta.html" %}
            {% end %}

            {% block content %}
            {% block page_root %}{% end %}
            {% end %}

            {% block body_after %}
            <script nonce="{{ csp_nonce() }}">
            document.body.addEventListener("htmx:configRequest", function(event) {
              var meta = document.querySelector('meta[name="csrf-token"]');
              if (meta) { event.detail.headers["X-CSRF-Token"] = meta.content; }
            });
            </script>
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
                  {{ csrf_field() }}
                  <input type="hidden" name="slug" value="{{ author_studio.slug }}">
                  <input type="hidden" name="mode" value="{{ author_studio.mode }}">
                  <input type="hidden" name="title" value="{{ author_studio.title }}">
                  <input type="hidden" name="source_revision" value="{{ author_studio.source_revision | default('') }}">
                  <label for="author-studio-source">Source</label>
                  <textarea id="author-studio-source"
                            name="source"
                            spellcheck="false"
                            data-source-path="{{ author_studio.source_path }}"
                            data-has-patitas-ast="{{ 'true' if author_studio.has_ast else 'false' }}">{{ author_studio.source_text }}</textarea>
                </form>
                <section aria-label="Rendered preview">
                  {% if author_studio.preview_html %}
                  {{ author_studio.preview_html }}
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
        "theme/views/author_dashboard.html": dedent(
            """\
            {% extends "shell.html" %}

            {% block page_root %}
            <main id="author-dashboard"
                  class="chirp-theme-author-dashboard__workspace"
                  data-author-dashboard
                  data-author-dashboard-errors="{{ author_dashboard.summary.error_count }}"
                  data-author-dashboard-warnings="{{ author_dashboard.summary.warning_count }}">
              <header>
                <p>Local authoring</p>
                <h1>Author dashboard</h1>
                <p>Import, freshness, and lint status for this local docs workspace.</p>
                <a href="/docs/_author/dashboard?json=1">JSON</a>
              </header>
              <section aria-label="Author workspace summary">
                <p>Mounts: {{ author_dashboard.summary.mount_count }}</p>
                <p>Pages: {{ author_dashboard.summary.page_count }}</p>
                <p>Blocking errors: {{ author_dashboard.summary.error_count }}</p>
              </section>
              {% if author_dashboard.blocking %}
              <section aria-label="Top blocking errors">
                <h2>Top blocking errors</h2>
                {% for item in author_dashboard.blocking %}
                <article data-severity="{{ item.severity }}">
                  <strong>{{ item.source_path }}{% if item.line %}:{{ item.line }}{% end %}</strong>
                  <span>{{ item.message }}</span>
                  {% if item.studio_url %}<a href="{{ item.studio_url }}">Open</a>{% end %}
                </article>
                {% end %}
              </section>
              {% end %}
              <section aria-label="Mounted sources">
                {% for mount in author_dashboard.mounts %}
                <article data-mount-id="{{ mount.id }}" data-mount-status="{{ mount.status }}">
                  <h2>{{ mount.label }}</h2>
                  <p>{{ mount.source_root }}</p>
                  <p>{{ mount.page_count }} page(s)</p>
                  <p>{{ mount.error_count }} error(s) / {{ mount.warning_count }} warning(s)</p>
                  {% for item in mount.formats %}
                  <span data-source-format="{{ item.format }}">{{ item.format }} {{ item.count }}</span>
                  {% end %}
                </article>
                {% end %}
              </section>
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

    files.update(_repository_files(args.starter, args.name))
    if args.starter == "api-portal":
        files.update(_api_portal_files(args.name))
    elif args.starter == "multi-mount":
        for path in (
            "content/_index.md",
            "content/docs/_index.md",
            "content/docs/get-started.md",
        ):
            files.pop(path)
        files.update(_multi_mount_files())

    written: list[Path] = []
    for rel, body in files.items():
        target = app_root / rel
        if target.exists() and not force:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        written.append(target)

    if not written:
        return CommandResult(
            command=command_name(args),
            ok=True,
            summary="no scaffold files written",
            data={
                "app_root": app_root,
                "written": [],
                "skipped_existing": True,
                "force": bool(force),
                "starter": args.starter,
            },
            terminal_lines=(
                f"no files written — {app_root} already has a Furatena scaffold (use --force)",
            ),
        )
    summary = f"initialized Furatena app at {app_root}"
    return CommandResult(
        command=command_name(args),
        ok=True,
        summary=summary,
        data={
            "app_root": app_root,
            "written": [path.relative_to(app_root) for path in written],
            "count": len(written),
            "force": bool(force),
            "starter": args.starter,
        },
        terminal_lines=(summary, *(f"  {path.relative_to(app_root)}" for path in written)),
    )


def configure(sub: Any) -> None:
    init = sub.add_parser("init", help="Scaffold a standalone Furatena app")
    init.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Target app directory (default current directory)",
    )
    init.add_argument("--name", default="Furatena Docs", help="Site name")
    init.add_argument(
        "--starter",
        choices=STARTERS,
        default="minimal",
        help="Maintained repository profile (default: minimal)",
    )
    init.add_argument("--force", action="store_true", help="Overwrite scaffold files")
    init.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    init.set_defaults(handler=_run_init)


COMMAND = CommandModule("init", configure)
