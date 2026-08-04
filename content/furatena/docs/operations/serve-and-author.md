---
title: Serve and author
owner: docs-product
reviewed_at: "2026-08-03"
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
# equivalent convenience launchers
make serve
./app/run
```

Default URL: http://127.0.0.1:8001/

The checkout command is canonical: both convenience launchers delegate to
`uv run fura serve` and preserve every flag and the CLI exit status. `./app/run`
defaults `CHIRP_SKIP_CONTRACT_CHECKS` to `1` for fast local dogfooding while preserving
an explicit caller value, for example `CHIRP_SKIP_CONTRACT_CHECKS=0 ./app/run --author`.

The app reads `app/docs.yaml`, loads mounts from `app/mounts.yaml`, and indexes markdown
under `content/`.

## Serve modes

| Mode | How | Use when |
|------|-----|----------|
| **Author** (default) | `uv run fura serve` | Editing content — live index + partial reload |
| **Author forced** | `uv run fura serve --author` | Ignore `app/frozen/` cache |
| **Preview** | `uv run fura serve --preview` | Prod-like — serve frozen shards only |
| **Hybrid** | `uv run fura serve` when `frozen/` exists | Fast startup with frozen assets, live content |

Legacy env aliases still work: `FURA_MODE=author`, `FURA_FROZEN=1` (preview).

## Server identity

Furatena uses Pounce's built-in display contract for the server banner. The default
identity shows the Furatena name and version with minimal signage; Pounce still owns
the final readiness signal. Set `POUNCE_APP_NAME`, `POUNCE_APP_TAGLINE`,
`POUNCE_APP_VERSION`, or `POUNCE_SIGNAGE` before `fura serve` to override those
defaults. Furatena preserves values supplied by the caller.

## Startup lifecycle

Normal author and hybrid startup freezes the Chirp application and runs its contract
suite exactly once. A successful preflight writes a compact catalog, check-count, elapsed
time, and effective reload summary to stderr. Warning details remain available through
`uv run fura check`; contract errors include their template or route origin and a recovery
action, then stop startup with a validation exit before the listener opens. Preview mode
and an explicit `CHIRP_SKIP_CONTRACT_CHECKS=1` report that checks were skipped.

Furatena does not print an `Open` or `Ready` line. Pounce shows the configured URL as
server information and owns readiness. Released Pounce 0.9.2 currently emits two
readiness log events in reload-enabled author mode—one after binding and another after
lifespan startup—so Furatena's one-ready lifecycle remains blocked on an upstream Pounce
fix and release. Furatena does not hide either event with a logging filter. Redirected
preflight output is plain, stable text without cursor controls or spinner artifacts. With
Chirp JSON logging configured, the Furatena preflight on stderr is one
`furatena.serve.preflight` JSON event so it composes with Pounce deployment logs.

`uv run fura serve --json` reserves stdout for one standard command result describing
the completed `preflight` phase. Its `data.ready` field is `false` and its
`data.configured_url` is configuration, not an early readiness claim; Pounce server and
readiness logs remain on stderr.

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
uv run fura serve --workers 8
```

Worker count also respects **`FURA_WORKERS`**.

That value controls catalog indexing. **`FURA_SERVER_WORKERS`** independently
controls Pounce serving processes; leave it unset for automatic local sizing.
The volume-backed Railway private image fixes it at one serving process.

## Version channels

Release notes under `releases/*.md` enable version channels. Select a channel:

```bash
FURA_CHANNEL=latest uv run fura serve          # default
FURA_CHANNEL=0.8.0 uv run fura serve           # specific release id
```

The docs rail shows a version selector when multiple channels exist for the active mount.

## Next

→ [[docs/operations/freeze-and-export|Freeze and export]] — snapshot the catalog for preview and static hosting.
