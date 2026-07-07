---
title: Operations
owner: docs-product
reviewed_at: "2026-07-07"
description: Preview, build, check, and deploy Furatena sites
draft: false
weight: 40
lang: en
type: doc
category: operations
icon: rocket

cascade:
  type: doc
---

Run Furatena locally, build static HTML, publish it, and validate docs in CI.

## Guides

| Page | Covers |
|------|--------|
| [[docs/operations/serve-and-author|Serve and author]] | Live index, reload, serve modes |
| [[docs/operations/freeze-and-export|Freeze and export]] | `fura freeze`, `fura export`, GitHub Pages |
| [[docs/operations/check-and-lint|Check and lint]] | `fura check`, content lint, theme lint |
| [[docs/operations/docs-quality|Gate documentation completeness]] | Orphans, navigation, links, public coverage, and reasoned exemptions |
| [[docs/operations/migrate-from-js-docs|Migrate from JS docs]] | MDX lowering and platform migration path |
| [[docs/operations/consume-agent-outputs|Consume agent outputs and MCP]] | llms, page indexes, sidecars, channels, and MCP |
| [[docs/operations/deployment-profiles|Deployment profiles]] | Local, static, cloud, and self-hosted operating paths |
| [[docs/operations/observability-and-recovery|Observability and incident recovery]] | Structured events, telemetry, rollout, rollback, backup, and recovery |
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
