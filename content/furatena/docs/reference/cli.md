---
title: CLI reference
description: fura subcommands and common flags
draft: false
weight: 10
lang: en
type: doc
tags: [cli, fura]
category: reference
---

The Furatena CLI is **`fura`**.

## Global flags

```bash
fura --app-root ./docs-site --config ./docs-site/docs.yaml <command>
```

| Flag | Effect |
|------|--------|
| `--app-root PATH` | App root containing `docs.yaml` |
| `--config PATH` | Explicit `docs.yaml` path |
| `--autodoc-config PATH` | Explicit autodoc config |

## Init

```bash
fura init ./docs-site --name "Acme Docs"
```

Scaffolds a standalone Furatena app with `docs.yaml`, `mounts.yaml`, starter content,
minimal view templates, search template, and branding assets.

## Serve

```bash
fura serve [--author] [--preview] [--workers N] [--port PORT]
```

| Flag | Effect |
|------|--------|
| `--author` | Force live catalog index |
| `--preview` | Serve frozen catalog only |
| `--workers N` | Parallel indexing pool size |

## Build

```bash
fura freeze [--workers N]
fura export
fura pdf [--page /docs/get-started/ | --collection docs]
```

`fura pdf` writes PDF artifacts for one public page, one collection, or the full
public site. The default output is `app/public/pdf/`; successful runs also refresh
`app/public/channels.json` unless `--no-channels` is set.

## Validate

```bash
fura check [--content-only] [--strict-edition-links]
fura query --directive tabs
fura docs-inventory --output docs/public-surface-inventory.json
```

`fura docs-inventory` derives CLI, route, configuration, MCP, sidecar,
diagnostic, and deployment-profile coverage from implementation metadata. It
links stable identifiers to existing docs and emits explicit missing/stale lists.
An existing output file is used as the default prior baseline for stale detection.

`fura docs-reference --output PATH` generates the exhaustive parser/configuration
reference. Add `--check` in CI to fail with `fura.docs_reference.drift` when the
committed page differs from active commands, options, defaults, config fields, or
environment controls.

## Migrate

```bash
fura migrate [--dry-run] [--keep-mdx]
fura migrate --report [--json]
```

`fura migrate --report` is read-only. It groups migration readiness findings by
severity, source path, construct, and suggested action before source files are
changed.

## Theme

```bash
fura theme list
fura theme inspect [PATH]
fura theme eject PATH
fura theme eject --all
fura theme diff PATH
fura theme init theme-skin
```

| Command | Effect |
|---------|--------|
| `theme list` | List docs-core ids (`theme.id`) and skin packs (`theme.use`) |
| `theme inspect [PATH]` | Show the active source and override target for templates/assets |
| `theme eject PATH` | Copy the resolved source into the local override path |
| `theme eject --all` | Copy every inspectable template/asset into local overrides |
| `theme diff PATH` | Compare a local override with the next upstream source |
| `theme init DIR` | Scaffold a reusable skin-pack directory |

Examples:

```bash
fura theme inspect views/doc.html
fura theme eject directives/callout.html
fura theme diff directives/callout.html
```

## Environment

| Variable | Purpose |
|----------|---------|
| `FURA_BASE_URL` | Public origin for canonical/OG URLs |
| `FURA_APP_ROOT` | Default app root when `--app-root` is omitted |
| `FURA_CHANNEL` | Version channel (`latest` or release id) |
| `FURA_FROZEN=1` | Legacy alias for preview mode |
| `FURA_WORKERS` | Default worker count for indexing |
