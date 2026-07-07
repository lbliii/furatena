---
title: Serve and author
owner: docs-product
reviewed_at: "2026-07-07"
description: fura serve modes, live reload, and author invalidation
draft: false
weight: 10
lang: en
type: doc
tags: [serve, author, reload, htmx]
category: operations
---

`fura serve` runs the Furatena hypermedia app: catalog graph + Kida views + htmx shell.

## Start the server

```bash
uv run fura serve
# or
./app/run
```

Default URL: http://127.0.0.1:8001/

The app reads `app/docs.yaml`, loads mounts from `app/mounts.yaml`, and indexes markdown
under `content/`.

## Serve modes

| Mode | How | Use when |
|------|-----|----------|
| **Author** (default) | `fura serve` | Editing content — live index + partial reload |
| **Author forced** | `fura serve --author` | Ignore `app/frozen/` cache |
| **Preview** | `fura serve --preview` | Prod-like — serve frozen shards only |
| **Hybrid** | `fura serve` when `frozen/` exists | Fast startup with frozen assets, live content |

Legacy env aliases still work: `FURA_MODE=author`, `FURA_FROZEN=1` (preview).

## What reloads automatically

| Change | Behavior |
|--------|----------|
| **Markdown** | Author pipeline — htmx partial swaps on the open page (`page-root`, `toc-panel`, `head-meta`) |
| **CSS** (lagoon / docs-core) | Hot-swap via Chirp dev reload — no full flash |
| **Kida templates** | Full browser refresh (template env rebuild) |

Edit a page while it is open in the browser — save markdown and watch fragments update
without restarting the server.

## Author stale polling

With auto-reload enabled, the shell polls **`GET /docs/_author/stale`** for dirty slugs
and triggers htmx refresh when invalidation hints change.

Invalidation regions come from Content IR diffs — body-only edits skip full graph rebuilds.

## Author dashboard

Open **`/docs/_author/dashboard`** while running author or hybrid mode to inspect
mounts, source formats, page counts, freshness, and lint blockers in the browser.
The dashboard is local-only author chrome; preview mode and static exports return 404
for author routes and do not include the dashboard.

## Parallel indexing

Speed up large corpora:

```bash
fura serve --workers 8
```

Worker count also respects **`FURA_WORKERS`**.

## Version channels

Release notes under `releases/*.md` enable version channels. Select a channel:

```bash
FURA_CHANNEL=latest fura serve          # default
FURA_CHANNEL=0.8.0 fura serve           # specific release id
```

The docs rail shows a version selector when multiple channels exist for the active mount.

## Next

→ [[docs/operations/freeze-and-export|Freeze and export]] — snapshot the catalog for preview and static hosting.
