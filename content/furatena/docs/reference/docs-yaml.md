---
title: docs.yaml reference
description: Furatena app configuration — shell, views, theme, site, compose
draft: false
weight: 20
lang: en
type: doc
tags: [config, docs-yaml]
category: reference
---

`app/docs.yaml` is the declarative configuration for a Furatena deployment.

## Top-level keys

| Key | Purpose |
|-----|---------|
| `shell` | Root shell template (default `shell.html`) |
| `views` | Map view kind → template path |
| `overrides` | Per-slug view template overrides |
| `compose` | Multi-node view data sources |
| `site` | Product branding — name, home hero, navigation |
| `theme` | Docs-core id, skin pack, tokens, effects |
| `mounts` | Path to `mounts.yaml` |
| `rewrites` | Path to URL rewrite map |
| `inventories` | Path to reference inventories |
| `i18n` | Optional multilingual config |

## Views map

```yaml
views:
  doc: views/doc.html
  doc_list: views/doc_list.html
  page: views/page.html
  home: views/home.html
  changelog: views/changelog.html
  collection: views/collection.html
  portal: views/portal.html
  default: views/doc.html

overrides:
  docs/get-started/installation: views/landing.html
```

Resolution order: front matter `view:` → home URL → `overrides:` → view kind → heuristics → `default`.

## Site branding

Variabilizes home hero, shell title, and top navigation without forking templates:

```yaml
site:
  name: Furatena
  tagline: Hypermedia documentation catalog
  description: >
    Furatena documentation — markdown indexed live, served as htmx fragments.
  mark: "𒀭"
  home:
    cta_primary:
      label: Get started
      href: /docs/get-started/
    cta_secondary:
      label: CLI reference
      href: /docs/reference/cli/
    hero_points:
      - Live catalog graph
      - htmx shell navigation
    metrics:
      - value: "1"
        label: markdown corpus
        hint: Index pages from content/ mounts at serve time.
    visual:
      title: Docs as data, HTML on demand.
      description: Markdown indexed into a live graph.
  navigation:
    documentation:
      menu_label: Documentation
      dropdown_href: /docs/
      overview:
        href: /docs/
        kicker: Explore
        title: Documentation
        blurb: Guides and reference for Furatena.
      links:
        - href: /docs/get-started/
          label: Get started
          blurb: Install and run your first site.
          icon: book-open
```

Omit `site.navigation` to use sensible defaults derived from `site.name`.

Templates receive: `site_name`, `site_tagline`, `site_description`, `site_mark`, `site_home`, `site_nav`.

## Theme

```yaml
theme:
  use: lagoon              # skin pack (furatena.themes entry point)
  id: furatena             # docs-core CSS bundle
  templates: theme/templates
  effects:
    code: flat             # flat | subtle | glow
    cards: flat            # flat | elevated
    hero: wash             # wash | minimal
  measure:
    prose: 80ch
    reading: 76ch
    docs: 80ch
    container: 90rem
  fonts:
    sans: Inter
    display: Inter
  overrides:               # optional per-file wins over skin pack
    tokens: theme/tokens.css
    styles: theme/styles.css
```

List packs: `fura theme list`. Scaffold a custom skin: `fura theme init`.

## Compose

```yaml
compose:
  collection:
    data: ../data/collections.yaml
```

Used by `layout: collection` pages — see [[docs/authoring/collections|Collections]].

## i18n (optional)

```yaml
i18n:
  default_language: en
  languages:
    - { code: en, name: English }
    - { code: es, name: Español }
locales_dir: locales
```

See [I18N.md](https://github.com/lbliii/furatena/blob/main/docs/I18N.md) in the repository.

## Related

- [[docs/reference/mounts-yaml|mounts.yaml]]
- [[docs/theming/tokens-and-skin|Tokens and skin]]
