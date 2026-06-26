---
title: Branding
description: site config, favicon, and PWA manifest
draft: false
weight: 30
lang: en
type: doc
tags: [branding, site, favicon]
category: theming
---

Product branding is configured in **`site:`** (`docs.yaml`) and static assets under
**`app/theme/assets/branding/`** — not hardcoded in view templates.

## Site config

```yaml
site:
  name: Furatena
  tagline: Live documentation from markdown
  description: >
    Short string for meta description fallback and Open Graph.
  mark: "𐂛"          # Linear B ideogram B141 — gold
  home:
    cta_primary:
      label: Get started
      href: /docs/get-started/
    hero_points:
      - Updates as you edit
      - Built-in search and navigation
  navigation:
    documentation:
      menu_label: Documentation
      links:
        - href: /docs/get-started/
          label: Get started
          blurb: Install and run your first site.
          icon: book-open
```

The default **`𐂛`** mark is Linear B Ideogram B141 — **gold** in Mycenaean palace
inventories (the same material as Muisca tunjos). Requires a Linear B-capable font
(Noto Sans Linear B is loaded in the docs shell).

Templates read **`site_name`**, **`site_home`**, **`site_nav`**, etc. — see
[[docs/reference/docs-yaml|docs.yaml reference]].

## Static assets

| File | Served at |
|------|-----------|
| `theme/assets/branding/favicon.svg` | `/docs-theme/branding/favicon.svg` |
| `theme/assets/branding/favicon-32x32.png` | `/docs-theme/branding/favicon-32x32.png` |
| `theme/assets/branding/site.webmanifest` | `/docs-theme/branding/site.webmanifest` |

`GET /favicon.ico` serves the branding icon for default browser requests.

Update **`site.webmanifest`** `"name"` to match `site.name`.

## Home view

`views/home.html` renders hero CTAs, metrics, and product visual from **`site.home`**.
Markdown body on `_index.md` still drives the prose section below the hero.

## JSON-LD and OG

Per-page meta comes from node title/description. Site name in JSON-LD uses **`site.name`**.
Set **`FURA_BASE_URL`** before deploy for correct canonical and OG URLs.

## Related

- [[docs/get-started/project-layout|Project layout]]
- [[docs/theming/tokens-and-skin|Tokens and skin]]
