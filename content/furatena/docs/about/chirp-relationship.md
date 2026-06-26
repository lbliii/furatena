---
title: Chirp relationship
description: Furatena on Chirp, Bengal cutover, and the retained Chirp fixture corpus
draft: false
weight: 20
lang: en
type: doc
tags: [chirp, bengal, ecosystem]
category: about
---

Furatena **runs on Chirp** and shares the Bengal hypermedia stack. It is the documentation
catalog layer; Chirp is the web framework underneath.

## Stack

```
Markdown (content/) → Furatena catalog → Kida views → Chirp App → browser
```

| Layer | Package |
|-------|---------|
| Web framework | `bengal-chirp` |
| UI components | `chirp-ui` |
| Markdown | `patitas` |
| Templates | Kida (via Chirp) |

Furatena requires a released **`bengal-chirp`** PyPI package (currently **0.8.2+**).

## Chirp fixture corpus

The production app dogfoods the Furatena corpus:

| Mount | URL | Purpose |
|-------|-----|---------|
| **furatena** (default) | `/`, `/docs/…` | Furatena product documentation |

The Chirp corpus remains in `content/chirp/` as a regression fixture. It proved every
directive, federation feature, and freeze path before Furatena became the primary site,
but it is no longer mounted in the default production app.

## GitHub Pages cutover (Wave 18)

Furatena replaced Bengal as the sole GitHub Pages builder for Chirp documentation:

- `.github/workflows/pages.yml` runs `fura freeze` + `fura export`
- No Bengal build loop in CI

Furatena can deploy **its own docs** the same way — this site is the reference implementation.

## Theming split

| Concern | Owner |
|---------|-------|
| Hypermedia runtime, routes, htmx | Chirp |
| Catalog graph, directives, check | Furatena (`src/furatena/`) |
| Docs chrome CSS (`.chirp-theme-*`) | Furatena docs-core (`theme.id: furatena`) |
| Skin / tokens | Lagoon pack (`theme.use: lagoon`) |
| Product branding | `site:` in `docs.yaml` + `theme/assets/branding/` |

CSS class prefix `chirp-theme-*` is a technical contract shared with chirp-ui — not Chirp
product branding.

## Local co-development

Uncomment `[tool.uv.sources]` in `pyproject.toml` to develop against a sibling Chirp checkout.

## Next

→ [[docs/about/roadmap|Roadmap]]
