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
```

Lists docs-core ids (`theme.id`) and skin packs (`theme.use`).

## Environment

| Variable | Purpose |
|----------|---------|
| `FURA_BASE_URL` | Public origin for canonical/OG URLs |
| `FURA_CHANNEL` | Version channel (`latest` or release id) |
| `FURA_FROZEN=1` | Legacy alias for preview mode |
| `FURA_WORKERS` | Default worker count for indexing |
