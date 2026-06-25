# Packaged theme bundle inventory

Classification of every module imported by `style.css` (bundled into `/docs-assets/theme.{hash}.css`).
Use this when evolving legacy Bengal toward native Fura docs-core: **retire superseded/dead
imports**, keep **bridge** utilities, evolve **active** surfaces in `chirp-theme.css`.

**Legend**

| Status | Meaning |
|--------|---------|
| **Active** | Still hit by Furatena docs templates or markup |
| **Bridge** | Token/effect primitives — keep; consumed via `--fura-effects-*` presets |
| **Superseded** | Legacy Bengal selectors replaced by chirp-ui + `directives.css` on docs path |
| **Dead** | Removed from bundle or no longer imported |

---

## Token foundation

| File | Status | Notes |
|------|--------|-------|
| `tokens/foundation.css` | Bridge | Primitive scales — implementation detail |
| `tokens/typography.css` | Bridge | Type scale |
| `tokens/semantic.css` | Bridge | `--elevation-*`, `--neumorphic-*`, `--color-*` bridge → `--chirpui-*` |

## Base

| File | Status | Notes |
|------|--------|-------|
| `base/reset.css` | Active | Inline code baseline |
| `base/typography.css` | Active | Prose defaults |
| `base/utilities.css` | Active | Shared utilities |
| `base/interactive-patterns.css` | Active | Focus/hover patterns |
| `base/accessibility.css` | Active | a11y helpers |
| `base/print.css` | Active | Print stylesheet |
| `base/transitions.css` | Active | Motion tokens |
| `composition/layouts.css` | Active | Layout primitives |

## Utilities (effect engine)

| File | Status | Notes |
|------|--------|-------|
| `utilities/motion.css` | Bridge | Shared transitions — used by components |
| `utilities/gradient-borders.css` | Bridge | Animated gradient borders — opt-in via `--fura-effects-code: glow` |

## Legacy page layouts (quarantine)

| File | Status | Notes |
|------|--------|-------|
| `layouts/grid.css` | **Dead** | Removed from bundle (Wave 2) — docs use `chirp-theme-docs-layout` |
| `layouts/header.css` | **Dead** | Removed from bundle (Wave 2) |
| `layouts/page-header.css` | **Dead** | Removed from bundle (Wave 2) |
| `layouts/footer.css` | **Dead** | Removed from bundle (Wave 2) |

## Components

| File | Status | Notes |
|------|--------|-------|
| `components/alerts.css` | **Dead** | Removed from bundle (Wave 2) — directives use `chirpui-callout` |
| `components/tabs.css` | **Dead** | Removed from bundle (Wave 2) — directives use `chirpui-tabs` |
| `components/tabs-native.css` | **Dead** | Removed from bundle (Wave 2) |
| `components/dropdowns.css` | **Dead** | Removed from bundle (Wave 2) — directives use `chirpui-collapse` |
| `components/checklist.css` | Active | Checklist markup in corpus |
| `components/steps.css` | Active | Steps directive wrapper classes |
| `components/target-anchor.css` | Active | Heading anchor helpers |
| `components/buttons.css` | Active | Shared button chrome |
| `components/forms.css` | Active | Form controls in docs |
| `components/tags.css` | Active | Tag pills |
| `components/code.css` | **Bridge** | `code-border-glow` animation — opt-in preset |
| `components/mermaid.css` | Active | Mermaid diagrams |
| `components/toc.css` | Active | TOC rail styling (packaged theme extends) |
| `components/navigation.css` | Active | Breadcrumbs, pagination primitives |
| `components/badges.css` | Active | Badges in tabs/cards |
| `components/labels.css` | Active | Labels |
| `components/icons.css` | Active | Icon sizing |
| `components/search.css` | Active | Search page |
| `components/search-modal.css` | Active | Shell search modal |
| `components/link-preview.css` | **Dead** | Removed from bundle (Wave 25) |
| `components/hero.css` | Active | Hero primitives (Fura overrides wash in `skin/hero.css`) |
| `components/page-hero.css` | **Dead** | Removed from bundle (Wave 21c) |
| `components/interactive.css` | **Dead** | Removed from bundle (Wave 21c) |
| `components/blog.css` | **Dead** | Removed from bundle (Wave 21c) — blog layout in `chirp-theme.css` |
| `components/component-specimen.css` | **Dead** | Removed from bundle (Wave 21c) |
| `components/share.css` | **Dead** | Removed from bundle (Wave 21c) |
| `components/author.css` | **Dead** | Removed from bundle (Wave 21c) |
| `components/widgets.css` | **Dead** | Removed from bundle (Wave 21c) |
| `components/stale-banner.css` | **Dead** | Removed from bundle (Wave 21c) |
| `components/empty-state.css` | **Dead** | Removed from bundle (Wave 25) |
| `components/meta.css` | Active | Meta rows |
| `components/reference.css` | Active | API reference tables |
| `components/tracks.css` | Active | Collection/track sections |
| `components/type-identity.css` | Active | Per-surface accent assignment |
| `components/versioning.css` | Active | Version callouts |
| `components/locale-switcher.css` | Active | i18n switcher |
| `components/_video-embed.css` | Active | Video embeds |
| `components/_audio-embed.css` | Active | Audio embeds |
| `components/_code-embed.css` | Active | Code embeds |
| `components/_figure.css` | Active | Figures |
| `components/_terminal-embed.css` | Active | Terminal embeds |
| `components/pagination.css` | **Dead** | Removed — chirp-ui pagination |

## Bespoke docs surface

| File | Status | Notes |
|------|--------|-------|
| `chirp-theme.css` | **Active** | Primary docs layout contract (`.chirp-theme-*`) — evolve here, not in Bengal components |

## Unlayered globals in `style.css`

| Rule | Status | Notes |
|------|--------|-------|
| `.page-content` / `.page-footer` | Active | TODO: migrate into `@layer chirp-theme` |
| `@layer pages { main { padding-block } }` | Active | Overridden by Fura rail-only in `styles.css` |

---

## Evolution checklist

When retiring a **Superseded** import:

1. Grep rendered docs HTML for legacy class names
2. Confirm `directives.css` or `chirp-theme.css` covers the chirp-ui markup path
3. Remove `@import` from `style.css`
4. Run `fura check` + visual smoke on `/docs/reference/`, `/docs/theming/`
5. Update this inventory

When adding effects:

1. Implement animation/shadow in **Bridge** files (`code.css`, `utilities/*`)
2. Expose via **`theme.effects`** in `docs.yaml` → `data-fura-effects-*`
3. Wire derived variables in `tokens.css`, application in `effects.css`
4. Do **not** hardcode `animation: none` in `directives.css` — use `--fura-effect-*` vars

## Fura layers (not in bundle)

| File | Role |
|------|------|
| `theme/tokens.css` | Brand palette + effect preset variables |
| `theme/styles.css` | Entry — `@import`s `skin/*`, `effects.css` |
| `theme/skin/` | Fonts, shell layout, hero, chrome (TOC anchors, nav), home, error |
| `theme/effects.css` | Preset application (glow, elevated cards) |
| `theme/directives.css` | chirp-ui directive skin |

These load **after** the bundled theme and win via `@layer docs.*`.
