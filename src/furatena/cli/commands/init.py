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

STARTERS = ("minimal", "api-portal", "multi-mount", "governed-preview")
SKINS = ("none", "lagoon")


def _repository_files(starter: str, name: str, *, skin: str) -> dict[str, str]:
    project_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "furatena"
    audiences = {
        "minimal": "A small repo-owned documentation site with the shortest path to static output.",
        "api-portal": "A DevRel portal combining authored guides with an OpenAPI reference.",
        "multi-mount": "A platform documentation hub with independently mounted product, SDK, and operations sources.",
        "governed-preview": "A protected, commit-bound Railway preview with human and agent review surfaces.",
    }
    first_edits = {
        "minimal": "content/docs/get-started.md",
        "api-portal": "content/docs/get-started.md and specs/openapi.yaml",
        "multi-mount": "content/product/docs/get-started.md, content/sdk/docs/sdk-quickstart.md, or content/operations/docs/runbook.md",
        "governed-preview": "content/docs/get-started.md and the preview policy in README.md",
    }
    lagoon_note = (
        ""
        if skin == "lagoon"
        else dedent(
            """\

            ## Packaged docs layout + Lagoon

            This scaffold uses the restrained `vanilla` layout. For the full docs
            chrome and Lagoon skin, re-init with `--skin lagoon` or set in `docs.yaml`:

            ```yaml
            presentation:
              layout: docs
              skin: lagoon
              trusted_capabilities: [scripts]
            ```
            """
        )
    )
    files = {
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

            ## Edit and preview

            ```bash
            uv python install 3.14t
            uv sync
            PYTHON_GIL=0 uv run fura serve
            ```

            Open the printed URL, then edit `{first_edits[starter]}`.

            ## Check, freeze, and export

            ```bash
            PYTHON_GIL=0 uv run fura check --content-only --warnings-as-errors
            PYTHON_GIL=0 uv run fura freeze
            PYTHON_GIL=0 uv run fura export --base-path ""
            ```

            The deployable site is written to `public/`; `frozen/` contains the portable
            catalog and agent sidecars.
            """
        )
        + lagoon_note,
    }
    if starter == "governed-preview":
        files.update(_governed_preview_repository_files())
        files[".gitignore"] += ".env.preview\n"
        files["README.md"] += dedent(
            """\

            ## Governed pull-request previews

            The checked-in Railway configuration builds every eligible internal PR from
            its immutable head SHA. Configure the project and sealed reviewer token by
            following `PREVIEWS.md`; then use the generated workflow for one GitHub check
            and one idempotently updated review comment.
            """
        )
    return files


def _governed_preview_repository_files() -> dict[str, str]:
    return {
        "railway.toml": dedent(
            """\
            [build]
            builder = "dockerfile"
            dockerfilePath = "Dockerfile"

            [deploy]
            startCommand = "sh /app/scripts/railway-start.sh"
            healthcheckPath = "/readyz"
            healthcheckTimeout = 300
            restartPolicyType = "on_failure"
            restartPolicyMaxRetries = 3
            numReplicas = 1
            overlapSeconds = 5
            drainingSeconds = 15

            [environments.pr.deploy]
            numReplicas = 1
            overlapSeconds = 0
            drainingSeconds = 0
            """
        ),
        "Dockerfile": dedent(
            """\
            FROM python:3.14-slim
            COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /usr/local/bin/uv
            ENV PYTHONUNBUFFERED=1 PYTHON_GIL=0 UV_PROJECT_ENVIRONMENT=/opt/venv \\
                PATH=/opt/venv/bin:$PATH FURA_MODE=preview FURA_WORKERS=1
            WORKDIR /app
            RUN uv python install 3.14t
            COPY pyproject.toml ./
            RUN uv sync --no-dev --no-install-project --python 3.14t
            ARG FURA_PR_PREVIEW=""
            ARG FURA_PREVIEW_PR_NUMBER=""
            ARG FURA_PREVIEW_SHA=""
            ARG FURA_PREVIEW_REVIEW_URL=""
            ARG RAILWAY_PUBLIC_DOMAIN=""
            ARG RAILWAY_GIT_COMMIT_SHA=""
            ENV FURA_PR_PREVIEW=$FURA_PR_PREVIEW \\
                FURA_PREVIEW_PR_NUMBER=$FURA_PREVIEW_PR_NUMBER \\
                FURA_PREVIEW_SHA=$FURA_PREVIEW_SHA \\
                FURA_PREVIEW_REVIEW_URL=$FURA_PREVIEW_REVIEW_URL \\
                RAILWAY_PUBLIC_DOMAIN=$RAILWAY_PUBLIC_DOMAIN \\
                FURA_BUILD_GIT_SHA=$RAILWAY_GIT_COMMIT_SHA
            COPY . .
            RUN uv sync --no-dev --python 3.14t \\
                && fura --app-root /app freeze --full --workers 1
            EXPOSE 8000
            CMD ["sh", "/app/scripts/railway-start.sh"]
            """
        ),
        "scripts/railway-start.sh": dedent(
            """\
            #!/usr/bin/env sh
            set -eu
            exec fura --app-root /app serve --preview --host 0.0.0.0 \\
              --port "${PORT:-8000}" --workers 1
            """
        ),
        "scripts/preview_report.py": dedent(
            """\
            #!/usr/bin/env python3
            \"\"\"Verify and publish the governed-preview contract.\"\"\"

            from __future__ import annotations

            from furatena.catalog.preview_reporting import main


            if __name__ == "__main__":
                raise SystemExit(main())
            """
        ),
        ".github/workflows/preview-report.yml": dedent(
            """\
            name: governed preview report

            on:
              pull_request_target:
                types: [opened, reopened, synchronize, closed]
              repository_dispatch:
                types: [furatena-preview]

            permissions:
              contents: read
              checks: write
              pull-requests: write

            jobs:
              report:
                if: >-
                  github.event.action == 'closed' ||
                  (github.event.pull_request.head.repo.full_name == github.repository &&
                   github.event.pull_request.user.type != 'Bot') ||
                  github.event_name == 'repository_dispatch'
                runs-on: ubuntu-latest
                env:
                  GITHUB_TOKEN: ${{ github.token }}
                  FURA_PREVIEW_AUTH_TOKEN: ${{ secrets.FURA_PREVIEW_AUTH_TOKEN }}
                  PR_NUMBER: ${{ github.event.pull_request.number || github.event.client_payload.pr_number }}
                  PREVIEW_STATE: ${{ github.event.action == 'closed' && 'removed' || (github.event_name == 'pull_request_target' && 'queued') || github.event.client_payload.state }}
                  EXPECTED_SHA: ${{ github.event.pull_request.head.sha || github.event.client_payload.expected_sha }}
                  PREVIEW_URL: ${{ github.event.client_payload.preview_url || '' }}
                  DETAILS_URL: ${{ github.event.client_payload.details_url || '' }}
                steps:
                  - uses: actions/checkout@v7.0.0
                    with:
                      ref: ${{ github.event.repository.default_branch }}
                  - uses: astral-sh/setup-uv@v8.2.0
                    with:
                      python-version: "3.14t"
                      enable-cache: true
                  - run: uv sync --no-dev
                  - run: >-
                      uv run python scripts/preview_report.py
                      --publish
                      --repository "${GITHUB_REPOSITORY}"
                      --pr-number "${PR_NUMBER}"
                      --state "${PREVIEW_STATE}"
                      --expected-sha "${EXPECTED_SHA}"
                      --origin "${PREVIEW_URL}"
                      --details-url "${DETAILS_URL}"
            """
        ),
        ".env.preview.example": dedent(
            """\
            FURA_PR_PREVIEW=1
            FURA_PREVIEW_PR_NUMBER=<pull-request-number>
            FURA_PREVIEW_SHA=<full-head-sha>
            FURA_PREVIEW_REVIEW_URL=https://github.com/<owner>/<repo>/pull/<number>
            # Keep FURA_PREVIEW_AUTH_TOKEN sealed; never commit it here.
            """
        ),
        "PREVIEWS.md": dedent(
            """\
            # Governed previews

            Enable Railway PR Environments from an explicit production base, keep bot
            environments disabled, and deny untrusted forks. Give production a Railway
            domain so each PR receives a unique domain automatically.

            Inject the four public identity variables from `.env.preview.example` and a
            fresh sealed `FURA_PREVIEW_AUTH_TOKEN` into each ephemeral environment. Never
            pass the token as a Docker build argument. Configure the same token as a
            short-lived GitHub Actions secret only while reporting conformance.

            Verify any ready deployment with:

            ```bash
            FURA_PREVIEW_AUTH_TOKEN=... python scripts/preview_report.py \\
              --state ready --origin https://<preview-domain> \\
              --expected-sha <full-head-sha>
            ```

            Close or merge the PR to remove the environment. Cap previews at one replica,
            disable overlap/draining for ephemeral environments, and close stale PRs to
            bound build minutes and active wall-clock cost.
            """
        ),
    }


def _presentation_block(*, skin: str) -> str:
    if skin == "lagoon":
        return dedent(
            """\
            presentation:
              layout: docs
              skin: lagoon
              trusted_capabilities: [scripts]
            """
        )
    # Skinless minimal path: packaged vanilla layout (self-contained CSS).
    return dedent(
        """\
        presentation:
          layout: vanilla
        """
    )


def _docs_yaml(
    name: str,
    *,
    skin: str,
    get_started_href: str,
    docs_href: str,
    nav_links: tuple[tuple[str, str, str, str], ...],
) -> str:
    link_yaml = "\n".join(
        (
            f"        - href: {href}\n"
            f"          label: {label}\n"
            f"          blurb: {blurb}\n"
            f"          icon: {icon}"
        )
        for href, label, blurb, icon in nav_links
    )
    site_name = json.dumps(name)
    return (
        _presentation_block(skin=skin) + "\n" + f"site:\n"
        f"  name: {site_name}\n"
        f"  tagline: Live documentation from markdown\n"
        f"  description: Write markdown. Get a fast, searchable docs site with static and agent exports.\n"
        f"  home:\n"
        f"    cta_primary:\n"
        f"      label: Get started\n"
        f"      href: {get_started_href}\n"
        f"    cta_secondary:\n"
        f"      label: Browse docs\n"
        f"      href: {docs_href}\n"
        f"    hero_points:\n"
        f"      - Edit markdown and see it live\n"
        f"      - Search and static export from the same corpus\n"
        f"      - Agent-readable catalog sidecars included\n"
        f"  navigation:\n"
        f"    documentation:\n"
        f"      menu_label: Documentation\n"
        f"      dropdown_href: {docs_href}\n"
        f"      overview:\n"
        f"        href: {docs_href}\n"
        f"        kicker: Explore\n"
        f"        title: Documentation\n"
        f"        blurb: Guides and reference for {site_name[1:-1]}.\n"
        f"      links:\n"
        f"{link_yaml}\n"
        f"    develop:\n"
        f"      menu_label: Develop\n"
        f"      dropdown_href: /develop/\n"
        f"      overview:\n"
        f"        href: /develop/\n"
        f"        kicker: Build\n"
        f"        title: Developer tools\n"
        f"        blurb: Structured outputs for search, references, and AI tools.\n"
        f"      links:\n"
        f"        - href: /develop/\n"
        f"          label: Develop overview\n"
        f"          blurb: Browse catalog exports and agent-readable sidecars.\n"
        f"          icon: code\n"
        f"\n"
        f"mounts: mounts.yaml\n"
    )


def _branding_files(name: str) -> dict[str, str]:
    return {
        "theme/assets/branding/favicon.svg": dedent(
            """\
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
              <rect width="64" height="64" rx="12" fill="#111827"/>
              <path d="M18 44V20h30v6H26v6h18v6H26v6z" fill="#f8fafc"/>
            </svg>
            """
        ),
        "theme/assets/branding/site.webmanifest": (
            f'{{"name":{json.dumps(name)},"short_name":{json.dumps(name)},'
            '"icons":[],"theme_color":"#111827","background_color":"#ffffff",'
            '"display":"standalone"}\n'
        ),
    }


def _friendly_content(name: str) -> dict[str, str]:
    return {
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
            title: Welcome to {name}
            description: A friendly starter site you can reshape into your product docs.
            layout: home
            ---

            # Welcome to {name}

            Your docs site is live. Start with the [Get started](/docs/get-started/)
            guide, then replace these pages with your real product content.
            """
        ),
        "content/docs/_index.md": dedent(
            """\
            ---
            title: Documentation
            description: Guides for adopting and shipping with your product.
            weight: 10
            ---

            # Documentation

            - [Get started](/docs/get-started/) — edit your first page while the server runs
            - [Write a guide](/docs/guides/write-a-guide/) — structure topics readers can scan
            - [Front matter](/docs/reference/frontmatter/) — title, description, weight, and layout
            """
        ),
        "content/docs/get-started.md": dedent(
            """\
            ---
            title: Get started
            description: From empty folder to a live docs page.
            weight: 20
            ---

            # Get started

            You already ran `fura serve` — this page is proof it worked.

            ## Edit this page

            Open `content/docs/get-started.md`, change a sentence, and save. In author
            mode the open page reloads without a full rebuild.

            ## Next steps

            1. Replace the home page copy in `content/_index.md`
            2. Add a guide under `content/docs/guides/`
            3. Run `fura check`, then `fura freeze` and `fura export` when you are ready to publish
            """
        ),
        "content/docs/guides/_index.md": dedent(
            """\
            ---
            title: Guides
            description: How-to topics for common reader jobs.
            weight: 30
            ---

            # Guides

            Start with [Write a guide](/docs/guides/write-a-guide/).
            """
        ),
        "content/docs/guides/write-a-guide.md": dedent(
            """\
            ---
            title: Write a guide
            description: Keep each page one job, one outcome.
            weight: 10
            ---

            # Write a guide

            Give each guide a single reader job. Link to related reference pages instead
            of duplicating them.
            """
        ),
        "content/docs/reference/_index.md": dedent(
            """\
            ---
            title: Reference
            description: Stable fields and contracts.
            weight: 40
            ---

            # Reference

            See [Front matter](/docs/reference/frontmatter/) for common page fields.
            """
        ),
        "content/docs/reference/frontmatter.md": dedent(
            """\
            ---
            title: Front matter
            description: Title, description, weight, and layout fields.
            weight: 10
            ---

            # Front matter

            Use YAML front matter for `title`, `description`, `weight`, and optional
            `layout` (for example `home` on the site root).
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
            title: Get started
            description: Edit a guide and an OpenAPI operation from one repository.
            weight: 20
            ---

            # Get started

            You already ran `fura serve` — this page is proof it worked.

            ## Edit the portal

            1. Change a sentence in this guide (`content/docs/get-started.md`)
            2. Edit an operation in `specs/openapi.yaml`

            Furatena projects OpenAPI operations into `/api/rest/`, search, static
            output, and agent sidecars from the same source contract.
            """
        ),
    }


def _multi_mount_files(name: str) -> dict[str, str]:
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
            f"""\
            ---
            title: Welcome to {name}
            description: Product guides from the default mount.
            layout: home
            ---

            # Welcome to {name}

            Start with the [Product quickstart](/docs/get-started/), then follow the
            [SDK quickstart](/sdk/docs/sdk-quickstart/) and
            [operations runbook](/operations/docs/runbook/).
            """
        ),
        "content/product/docs/_index.md": dedent(
            """\
            ---
            title: Product guides
            description: Adopt and use the product.
            weight: 10
            ---

            # Product guides

            - [Product quickstart](/docs/get-started/) — first successful product workflow
            """
        ),
        "content/product/docs/get-started.md": dedent(
            """\
            ---
            title: Product quickstart
            description: First successful product workflow.
            weight: 20
            ---

            # Product quickstart

            You already ran `fura serve` — this page is proof the default mount works.

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

            Start with the [SDK quickstart](/sdk/docs/sdk-quickstart/).
            """
        ),
        "content/sdk/docs/sdk-quickstart.md": dedent(
            """\
            ---
            title: SDK quickstart
            description: Install and call the SDK.
            ---

            # SDK quickstart

            This page is owned by the `sdk` mount and publishes below `/sdk/`. Edit
            `content/sdk/docs/sdk-quickstart.md` to reshape it for your product.
            """
        ),
        "content/operations/_index.md": dedent(
            """\
            ---
            title: Operations
            description: Operator guidance from an independent mount.
            ---

            # Operations

            Start with the [service runbook](/operations/docs/runbook/).
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


def _shared_nav_links() -> tuple[tuple[str, str, str, str], ...]:
    return (
        (
            "/docs/get-started/",
            "Get started",
            "Edit your first page while the local server runs.",
            "book-open",
        ),
        (
            "/docs/guides/",
            "Guides",
            "How-to topics for common reader jobs.",
            "pencil",
        ),
        (
            "/docs/reference/",
            "Reference",
            "Stable fields such as front matter and layout.",
            "code",
        ),
    )


def _multi_mount_nav_links() -> tuple[tuple[str, str, str, str], ...]:
    return (
        (
            "/docs/get-started/",
            "Product quickstart",
            "First successful product workflow on the default mount.",
            "book-open",
        ),
        (
            "/sdk/docs/sdk-quickstart/",
            "SDK quickstart",
            "Install and call the SDK from an independent mount.",
            "code",
        ),
        (
            "/operations/docs/runbook/",
            "Operations runbook",
            "Verify and recover the documentation service.",
            "rocket",
        ),
    )


def _run_init(args: argparse.Namespace) -> CommandResult:
    app_root = Path(args.directory).expanduser().resolve()
    force = args.force
    skin = str(args.skin)

    if args.starter == "multi-mount":
        files = {
            "docs.yaml": _docs_yaml(
                args.name,
                skin=skin,
                get_started_href="/docs/get-started/",
                docs_href="/docs/",
                nav_links=_multi_mount_nav_links(),
            ),
            **_multi_mount_files(args.name),
            **_branding_files(args.name),
        }
    else:
        files = {
            "docs.yaml": _docs_yaml(
                args.name,
                skin=skin,
                get_started_href="/docs/get-started/",
                docs_href="/docs/",
                nav_links=_shared_nav_links(),
            ),
            **_friendly_content(args.name),
            **_branding_files(args.name),
        }
        if args.starter == "api-portal":
            files.update(_api_portal_files(args.name))

    files.update(_repository_files(args.starter, args.name, skin=skin))

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
                "skin": skin,
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
            "skin": skin,
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
    init.add_argument(
        "--skin",
        choices=SKINS,
        default="lagoon",
        help="Presentation skin (default: lagoon on the docs layout; none selects vanilla)",
    )
    init.add_argument("--force", action="store_true", help="Overwrite scaffold files")
    init.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    init.set_defaults(handler=_run_init)


COMMAND = CommandModule("init", configure)
