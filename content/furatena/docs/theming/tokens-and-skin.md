---
title: Tokens and skin
description: theme.use, theme.id, lagoon pack, and effect presets
draft: false
weight: 10
lang: en
type: doc
tags: [theming, tokens, lagoon]
category: theming
---

Furatena theming uses **two installable packs**:

| Config key | Selects | Built-in |
|------------|---------|----------|
| **`theme.id`** | Docs-core CSS (`.chirp-theme-*` surfaces, icons) | `furatena` |
| **`theme.use`** | Skin pack (tokens, skin CSS, JS, fonts) | `lagoon` |

```yaml
theme:
  use: lagoon
  id: furatena
  effects:
    code: flat      # flat | subtle | glow
    cards: flat     # flat | elevated
    hero: wash      # wash | minimal
  measure:
    prose: 80ch
    docs: 80ch
    container: 90rem
  fonts:
    sans: Inter
    display: Inter
```

Effect presets map to `data-fura-effects-*` attributes on `<html>`.

## Stylesheet stack

Author mode loads (in order):

1. `/static/chirpui.css` — chirp-ui components
2. `/docs-assets/theme.{hash}.css` — bundled docs-core from `theme.id`
3. `/docs-theme/tokens/tokens.css` — lagoon tokens
4. `/docs-theme/generated/theme-preset.css` — from `measure` + `fonts`
5. `/docs-theme/local/styles.css` — lagoon skin
6. `/docs-theme/local/directives.css` — directive skin

## Override tokens without forking

Point at project-local CSS:

```yaml
theme:
  use: lagoon
  overrides:
    tokens: theme/tokens.css
    styles: theme/styles.css
```

Or scaffold a full skin:

```bash
fura theme init my-brand/
```

## Dev reload

CSS hot-swaps via Chirp dev reload. Edit lagoon tokens or skin files and save — changes
appear within seconds without `fura export`.

## Related

- [[docs/theming/views-overrides|Views and overrides]]
- [[docs/reference/docs-yaml|docs.yaml reference]]
