---
title: Project layout
description: Where app config, content corpora, and the Furatena library live
draft: false
weight: 30
lang: en
type: doc
tags: [layout, configuration]
keywords: [app, content, docs.yaml, mounts.yaml]
category: onboarding
---

A Furatena deployment separates **app config**, **content corpora**, and the **library**.

```
app/           Default deployment (docs.yaml, theme, mounts)
content/       Markdown corpora (content/furatena = this site)
data/          Collections, glossary, URL rewrites
src/furatena/  Catalog runtime, directives, CLI
docs/          Furatena design notes (Dual IR, views, roadmap)
tests/         Catalog and runtime tests
```

## App configuration

| File | Role |
|------|------|
| `app/docs.yaml` | Shell, views, theme, **site branding**, compose hooks |
| `app/mounts.yaml` | Content mounts — which corpus is default, URL prefixes |
| `app/theme/` | Shell, views, branding assets, template overrides |
| `app/inventories.yaml` | External reference inventories |

### Site branding (`site:` in docs.yaml)

Product name, home hero CTAs, and top navigation copy live in `app/docs.yaml`:

```yaml
site:
  name: Furatena
  tagline: Hypermedia documentation catalog
  mark: "𒀭"
  home:
    cta_primary:
      label: Get started
      href: /docs/get-started/
```

Templates read `site_name`, `site_home`, and `site_nav` — no hardcoded product strings in the theme.

## Content mounts

Each mount points at a directory under `content/`:

```yaml
mounts:
  - id: furatena
    content_root: ../content/furatena
    default: true
  - id: chirp
    content_root: ../content/chirp
    url_prefix: /chirp
```

The default mount serves `/` and `/docs/…`. Additional mounts get a URL prefix.

## Theme tiers

| Tier | Config | What it controls |
|------|--------|------------------|
| docs-core | `theme.id: furatena` | Bundled `.chirp-theme-*` CSS + icons |
| skin pack | `theme.use: lagoon` | Tokens, skin overrides, JS |
| app branding | `site:` + `theme/assets/branding/` | Name, favicon, web manifest |

See `docs/THEMING.md` in the repository for theme customization.

## Next

→ [[docs/concepts/catalog-graph|Catalog graph]] — how pages become nodes and edges.
