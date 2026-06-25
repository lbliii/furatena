---
title: Roadmap
description: Furatena delivery waves and what shipped
draft: false
weight: 30
lang: en
type: doc
tags: [roadmap]
category: about
---

Furatena is delivered in **waves** — each adds catalog, authoring, or production capability
without breaking prior exports.

Full detail lives in [ROADMAP.md](https://github.com/lbliii/furatena/blob/main/docs/ROADMAP.md)
in the repository. Summary below.

## Shipped highlights

| Wave | Theme |
|------|-------|
| **1–3** | Native directives, Kida templates, platform hooks (freeze, search, SEO) |
| **4–6** | Discoverability, content parity, product surface (`fura` CLI, autodoc) |
| **7–9** | Production hardening, graph platform, semantic retrieval |
| **10–14** | Dual IR, content validation, incremental reload, agent-native catalog |
| **15–17** | Reference inventories, link audit, parallel indexing |
| **18–23** | Bengal cutover, DCP schema, theme consolidation, shell maturity |

## Recent themes

**Wave 21–23 — Theming and shell**

- `theme.id: furatena` docs-core pack + `theme.use: lagoon` skin
- `site:` branding config in `docs.yaml`
- Vendored htmx, favicon route, error page recovery

**Content rewrite (in progress)**

- `content/furatena/` — Furatena product documentation (this site)
- `content/chirp/` — retained for regression and Chirp deploy at `/chirp/`

## What's next

Likely follow-ups (not committed dates):

- Expand **theming** docs and `fura theme init` workflow in the user corpus
- **Autodoc** mount for `src/furatena/` API at `/api/`
- **i18n** user guide when multilingual mounts ship broadly
- Deeper **operations** runbooks (authoring at scale, multi-team mounts)

Track issues and PRs on [github.com/lbliii/furatena](https://github.com/lbliii/furatena).

## Design docs

| Doc | Topic |
|-----|-------|
| [DUAL_IR.md](https://github.com/lbliii/furatena/blob/main/docs/DUAL_IR.md) | Content + Presentation IR |
| [VIEWS.md](https://github.com/lbliii/furatena/blob/main/docs/VIEWS.md) | View kinds and resolution |
| [THEMING.md](https://github.com/lbliii/furatena/blob/main/docs/THEMING.md) | Theme tiers and packs |
| [DCP.md](https://github.com/lbliii/furatena/blob/main/docs/DCP.md) | Catalog protocol v3 |
