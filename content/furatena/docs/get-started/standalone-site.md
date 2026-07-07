---
title: Standalone site
description: Start a Furatena docs app without copying this repository's app directory.
weight: 18
lang: en
type: doc
tags: [standalone, init, cli]
category: onboarding
---

# Standalone site

Use `fura init` when you want Furatena as the docs platform for another project.
Choose the maintained repository profile that matches the team adopting it:

| Starter | Audience | First path |
|---------|----------|------------|
| `minimal` | Solo maintainers and small product teams that want the shortest Markdown-to-static path | Edit `content/docs/get-started.md`, then check, freeze, and export |
| `api-portal` | DevRel and API teams combining guides with an OpenAPI reference | Edit the guide and `specs/openapi.yaml`; inspect `/api/rest/` |
| `multi-mount` | Platform documentation teams federating separately owned product, SDK, and operations trees | Edit one mount, verify cross-mount navigation, then export the combined catalog |

```bash
mkdir docs-site
cd docs-site
fura init --name "Acme Docs" --starter minimal
fura check --content-only --warnings-as-errors
fura serve
```

The scaffold writes:

| Path | Purpose |
|------|---------|
| `docs.yaml` | Site, theme, view, and mount configuration |
| `mounts.yaml` | Content mount definitions |
| `content/` | Markdown corpus |
| `theme/` | Minimal shell, views, search page, and branding |
| `pyproject.toml` | CPython 3.14 and exact Furatena release dependency |
| `.github/workflows/docs.yml` | GIL-disabled clone-to-check-to-export verification |

The generated workflow installs CPython `3.14t`, sets `PYTHON_GIL=0`, proves
the GIL is disabled, and runs check, freeze, and export before uploading the
static artifact. The dependency is pinned to the same Furatena version that
generated the repository.

## API portal starter

```bash
fura init ./api-docs --name "Acme API" --starter api-portal
cd api-docs
uv sync
PYTHON_GIL=0 uv run fura check --content-only --warnings-as-errors
PYTHON_GIL=0 uv run fura freeze
PYTHON_GIL=0 uv run fura export --base-path ""
```

`config/autodoc.yaml` projects `specs/openapi.yaml` into the API reference,
search index, static site, and agent artifacts. The included operation is a
complete, lint-clean example rather than placeholder prose.

## Multi-mount starter

```bash
fura init ./platform-docs --name "Acme Platform" --starter multi-mount
cd platform-docs
uv sync
PYTHON_GIL=0 uv run fura check --content-only --warnings-as-errors
PYTHON_GIL=0 uv run fura freeze
PYTHON_GIL=0 uv run fura export --base-path ""
```

The default product mount owns `/`; the SDK and operations mounts publish at
`/sdk/` and `/operations/`. Each source tree remains independently owned while
one generated catalog and static artifact preserve cross-mount navigation.

## Target another app root

Every command can target an app root explicitly:

```bash
fura --app-root ./docs-site serve
fura --app-root ./docs-site check --content-only
fura --app-root ./docs-site query --heading "Get started"
```

Use `--config path/to/docs.yaml` when the config file does not live at
`APP_ROOT/docs.yaml`.

## Why this matters

The dogfood repository keeps a rich `app/` for Furatena itself, but a product team should
not need that structure. A standalone app only needs configuration, content, and optional
theme overrides; the catalog runtime, directives, search, and agent exports come from the
installed package.

## Customize the theme

Inspect a template before editing it:

```bash
fura theme inspect views/doc.html
fura theme eject directives/callout.html
fura theme diff directives/callout.html
```

See [[docs/theming/customization-cli|Customization CLI]] for the full override workflow.
