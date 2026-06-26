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
```

## Validate

```bash
fura check [--content-only] [--strict-edition-links]
fura query --directive tabs
```

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
