---
title: Operations
description: Serve, freeze, export, check, and deploy Furatena sites
draft: false
weight: 40
lang: en
type: doc
category: operations
icon: rocket

cascade:
  type: doc
---

Run Furatena locally, freeze for preview, export static HTML, and validate in CI.

## Guides

| Page | Covers |
|------|--------|
| [[docs/operations/serve-and-author|Serve and author]] | Live index, reload, serve modes |
| [[docs/operations/freeze-and-export|Freeze and export]] | `fura freeze`, `fura export`, GitHub Pages |
| [[docs/operations/check-and-lint|Check and lint]] | `fura check`, content lint, theme lint |
| [[docs/operations/deploy|Deploy]] | `FURA_BASE_URL`, channels, CI |

## Command cheat sheet

```bash
uv run fura serve              # author mode (hybrid when app/frozen/ exists)
uv run fura serve --author     # force live index
uv run fura serve --preview    # frozen only — prod-like
uv run fura freeze             # catalog IR + HTML → app/frozen/
uv run fura export             # static HTML → app/public/
uv run fura check              # hypermedia + content + theme lint
fura check --content-only      # skip Chirp app.check — corpus lint only
```

Set **`FURA_BASE_URL`** before freeze or export so canonical and Open Graph URLs point at
your public origin.
