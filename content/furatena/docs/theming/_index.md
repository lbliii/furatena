---
title: Theming
description: Tokens, skin packs, views, and branding
draft: false
weight: 35
lang: en
type: doc
category: theming
icon: palette

cascade:
  type: doc
---

Furatena separates **data** (catalog), **views** (page templates), and **theme** (look-and-feel).
Most rebrands need only tokens and `site:` branding — not a template fork.

## Guides

| Page | Covers |
|------|--------|
| [[docs/theming/tokens-and-skin|Tokens and skin]] | `theme.use`, lagoon, effects |
| [[docs/theming/views-overrides|Views and overrides]] | Template loader stack |
| [[docs/theming/customization-cli|Customization CLI]] | Inspect, eject, and diff overrides |
| [[docs/theming/branding|Branding]] | `site:`, favicon, web manifest |

## Four tiers

| Tier | Effort | What you change |
|------|--------|-----------------|
| **1. Tokens** | ~5 min | CSS variables — accent, fonts |
| **2. Skin** | ~1 hour | Spacing, hero, TOC, search chrome |
| **3. Templates** | ~1 day | View/partial/directive markup |
| **4. Theme package** | days | Reusable `furatena.themes` entry point |

Tier 1–2 should cover most product docs. If tier 3 is required for a visual change, consider
improving the default theme rather than forking the whole app.

## Quick start

```bash
fura theme list          # docs-core ids + skin packs
fura theme inspect views/doc.html
fura theme eject directives/callout.html
fura theme init          # scaffold custom skin directory
```

```yaml
# app/docs.yaml
theme:
  use: lagoon
  id: furatena
site:
  name: Your Product
  mark: "◆"
```

Design reference: [THEMING.md](https://github.com/lbliii/furatena/blob/main/docs/THEMING.md) in the repository.
