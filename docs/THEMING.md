# Furatena theming

Furatena separates **data** (catalog graph), **views** (how nodes render), and **theme** (look-and-feel). There is no build step for production — in dev, edits reload automatically (see below).

Reusable presentation extensions use the strict, versioned layout/skin/override contract in
[PRESENTATION_PACKS.md](PRESENTATION_PACKS.md). The `theme.id`, `theme.use`, `theme/`, and
`templates/` behaviors below remain the documented compatibility path.

**Views architecture:** see [VIEWS.md](VIEWS.md) for view kinds, resolution order,
folder layout, Kida composition rules, and how views differ from shell, partials,
and directives.

## Dev reload

When you run `./app/run` or `fura serve`, the app watches your work and refreshes without a static export loop:

- **CSS** (lagoon skin + docs-core bundle) — browser hot-swap via Chirp dev reload (no full page flash).
- **HTML / Kida templates** — full browser refresh (template env is rebuilt on reload).
- **Markdown** — handled by the docs author pipeline (htmx partial swaps on the open page, not a full reload). See [README.md](README.md#dev-workflow).

Edit CSS or templates and save — you should see changes within a second or two without running `fura export`.

## Four tiers of customization

| Tier | What you change | Where | Effort |
|------|-----------------|-------|--------|
| **1. Tokens** | CSS variables (`--chirpui-accent`, fonts) | lagoon pack `tokens.css` (or override) | ~5 min |
| **2. Skin** | Layout spacing, hero, TOC, search chrome | lagoon pack `styles.css` + `skin/*` | ~1 hour |
| **3. Templates** | Markup for views, partials, directives | See loader stack below | ~1 day |
| **4. Theme package** | Ship a reusable theme others can `use:` | `furatena.themes` entry point + optional overrides | days |

Tier 1–2 should cover most rebrands. If tier 3 is required for a visual change, the default theme may need improvement — not a fork of the whole app.

## Template loader stack

Kida resolves templates **first match wins**:

```
1. templates/              ← project overrides (sparse — only files you shadow)
2. theme/templates/        ← theme overrides of framework partials/directives
3. theme/                  ← shell, views (presentation surfaces)
4. catalog/_templates/     ← framework defaults (directives, partials, search)
5. chirp-ui templates      ← component macros
```

Configure paths in `docs.yaml`:

```yaml
theme:
  use: lagoon
  id: chirp
  templates: theme/templates   # optional shadow dir
  overrides:                   # optional per-path wins over pack
    tokens: theme/tokens.css
```

Registered packs: `fura theme list` — **docs-core** via `theme.id` (built-in: `chirp`), **skin** via `theme.use` (built-in: `lagoon`).

### Scaffold a custom skin

```bash
fura theme init              # writes app/theme-skin/ by default
fura theme init my-brand/    # custom directory
```

The scaffold includes `tokens.css`, `styles.css`, `skin/*`, and a branding README. Wire overrides in `docs.yaml` (see generated README) or register a `furatena.themes` entry point for a reusable pack.

### Develop export previews

Machine-readable exports (`/catalog.json`, `/llms.txt`, …) stay canonical for agents. Human-readable previews live under **`/develop/`** and **`/develop/{id}/`** with truncated samples and download links to the raw URLs.

### What lives where

| Location | Role | Examples |
|----------|------|----------|
| `templates/` | Project overrides | `partials/head_meta.html`, `views/doc.html` |
| `theme/templates/` | Theme overrides of framework | `directives/child_cards.html`, `partials/docs_sidebar.html` |
| `theme/` | App shell, views, branding | `shell.html`, `views/doc.html`, `assets/branding/` |
| `src/furatena/themes/lagoon/` | Installable skin pack | `tokens.css`, `skin/*`, `js/*`, fonts |
| `src/furatena/themes/chirp/` | Installable docs-core bundle | `assets/css/style.css`, icons |
| `theme/views/` | Registered view templates | `doc.html`, `home.html`, `collection.html` |
| `templates/views/` | Project view overrides | `views/doc.html` (sparse) |
| `catalog/_templates/` | Framework plumbing (do not edit in apps) | `directives/*`, `partials/*`, `search.html` |

**Override one file without forking:** drop `templates/partials/docs_sidebar.html` in your project, or `theme/templates/partials/docs_sidebar.html` in your theme pack.

## Error pages

HTML errors share one framework template (`catalog/_templates/error.html`) rendered through the app shell. Handlers cover **404**, **403**, **405**, **413**, and **500** with status-specific copy, recovery links, and hypermedia recovery on missing pages.

| Status | Search panel | Extra context |
|--------|--------------|---------------|
| **404** | Yes — path-derived query + live `/errors/suggest` fragment | Keyword “Did you mean?” + semantic “Related pages” |
| **403** | No | Access denied message |
| **405** | No | Allowed HTTP methods from the `Allow` header |
| **413** | No | Payload limit message |
| **500** | No | Generic server error |

### Hypermedia recovery (404)

On a missing page the template:

1. Derives a search query from the URL path (e.g. `/docs/reference/routing/` → “reference routing”).
2. Prefills an htmx search input that swaps `#error-suggest-panel` via **`GET /errors/suggest?q=`**.
3. Splits hybrid results into **keyword** and **semantic-only** groups (TF-IDF chunk retrieval).
4. On boosted navigation, swaps `#page-root` and OOB-updates head meta via `partials/error_meta_oob.html`.

### Override the error page

Shadow the template like any other framework partial:

```
theme/templates/error.html          ← wins over catalog/_templates/error.html
theme/templates/partials/error_suggest_panel.html
templates/error.html                ← project-level override (highest priority)
```

The default template exposes an **`{% block error_content %}`** hook. Copy `error.html` into your shadow dir and edit that block, or replace the whole file — keep `layouts/docs_app.html` if you want the site nav and boost contract.

Registered in `fura check` via `Template("error.html")`; shadow files are picked up automatically when `theme.templates` is configured in `docs.yaml`.

## Views vs shell

See [VIEWS.md](VIEWS.md) for the full guide. Summary:

| Concept | File | Purpose |
|---------|------|---------|
| **Document frame** | `catalog/_templates/layouts/fura_shell.html` | Native HTML shell — doctype, htmx, `#main` blocks |
| **Shell** | `theme/shell.html` | Fura app shell — effects bootstrap, theme CSS, search modal |
| **View** | `theme/views/*.html` | Full `#page-root` page for a catalog node |
| **View kind** | front matter `layout:` / `kind:` | Author label that selects a view via `docs.yaml` |

View resolution (`ViewRegistry`):

1. Front matter `view:` or legacy `template:`
2. Home URL → `views/home.html`
3. `docs.yaml` `overrides:` per slug
4. View kind (`node.layout`) → `views.*` map (e.g. `collection`, `doc_list`)
5. Section-root heuristic (`doc` + children → `doc_list`)

## CSS stack

Linked stylesheets (in order):

1. `chirpui.css` — component baseline (do not theme here)
2. `/docs-assets/theme.{hash}.css` — bundled docs-core (`theme.id: chirp`)
3. `/docs-theme/tokens/tokens.css` — skin pack brand variables (lagoon)
4. `/docs-theme/generated/theme-preset.css` — measure + font stacks from `docs.yaml`
5. `/docs-theme/local/styles.css` — skin overrides (`@layer docs.overrides`)
6. `/docs-theme/local/directives.css` — directive skin for chirp-ui/Alpine markup (`@layer docs.directives`)

Author mode bundles CSS on first serve (cached in `.docs-cache/`). Freeze copies hashed assets to `frozen/assets/` and writes `renderer.fingerprint` beside the catalog export.

## Effect presets

Visual effects from the legacy Bengal bundle (glow, elevation, neumorphic shadows) are **opt-in**
via `docs.yaml` — not stripped by default overrides anymore.

```yaml
theme:
  use: lagoon              # furatena.themes entry point (built-in lagoon skin)
  id: chirp                 # packaged docs-core bundle selector
  templates: theme/templates
  overrides:                # optional — paths relative to app root
    tokens: theme/tokens.css
  effects:
    code: flat      # flat | subtle | glow
    cards: flat     # flat | elevated
    hero: wash      # wash | minimal
  measure:
    prose: 80ch
    reading: 76ch
    docs: 80ch
    container: 90rem
  fonts:
    sans: Inter
    display: Inter
```

These set `data-fura-effects-*` on `<html>` at boot and emit `/docs-theme/generated/theme-preset.css`
for reading width + font stacks. Brand colors stay in `theme/tokens.css`.

| Preset | What it does |
|--------|----------------|
| `code: flat` | Current flat docs code blocks (default) |
| `code: subtle` | `--elevation-card` on code wrappers |
| `code: glow` | Bengal `code-border-glow` animation on `pre` |
| `cards: elevated` | Card/tab/dropdown elevation + hover lift |
| `hero: wash` | Lagoon gradient + accent rail (default) |
| `hero: minimal` | No wash or rail — metadata pill only |

To try glow locally, set `theme.effects.code: glow` in `app/docs.yaml` and hard-refresh.

Packaged bundle module status: [`BUNDLE_INVENTORY.md`](../src/furatena/themes/chirp/assets/css/BUNDLE_INVENTORY.md).

## Token map (Tier 1)

| Concern | Variables |
|---------|-----------|
| Brand | `--chirpui-accent`, `--chirpui-accent-secondary`, `--chirpui-on-accent` |
| Surfaces | `--chirpui-bg`, `--chirpui-surface`, `--chirpui-border` |
| Hero wash | `--color-primary`, `--color-primary-light` (structure in `styles.css`) |
| Code | `--color-bg-code`, `--chirpui-code-bg` |
| Measure | `--chirpui-prose-max-width`, `--chirpui-docs-reading-measure`, `--chirpui-container-max` |
| Effects | `--fura-effect-code-*`, `--fura-effect-card-*` (set by presets) |

Edit `theme/tokens.css` only — never `:root` without a theme selector (breaks dark mode).

## Directive skin contract

Furatena is self-contained at runtime: docs-core CSS/icons ship in `furatena.themes.chirp`;
skin scripts live in the lagoon pack (`theme.use`).
Each surface follows:

```
Patitas handler → Kida + chirp-ui/Alpine template → directives.css → packaged theme
```

| Layer | Owns |
|-------|------|
| Handlers | Parse directive options, pick template |
| Kida templates | chirp-ui macros, `chirp-theme-directive-*` hooks |
| `directives.css` | Visual fidelity to chirp-theme for directive markup |
| Packaged theme | Tokens, typography, code.css, TOC chrome |

Code fences and code-tabs panels are wrapped server-side via `directives/code_block.html` (copy button markup is in HTML; JS only handles clicks).

## Runtime enhancements

Page chrome inside `#page-root` is ephemeral — it swaps on boosted navigation.

**Single lifecycle:**

```
htmx:afterSettle → syncDocsChrome() → FuraDocs.enhance.refresh(#page-root)
```

Cold load: `docs-enhance.js` bootstraps the same `refresh()` after defer scripts load.

Registered modules (`theme/js/docs-enhance.js`):

| Module | Role | Scope |
|--------|------|-------|
| `body-surface` | Mirror `#page-root` surface → body classes | document |
| `code-copy` | Clipboard clicks on `[data-fura-copy-code]` | document (once) |
| `page-actions` | Copy URL / LLM text and AI share actions (`chirpuiPopover`) |
| `toc` | Scroll-spy + active section (`fura-toc.js`) | `#page-root` |
| `docs-nav` | Sidebar disclosure (`fura-nav.js`) | `#page-root` |

Add a module with `FuraDocs.enhance.register(name, { enhance, cleanup })`.

Shell-only glue (search modal, mobile drawer, version select, author SSE/poll fallback) lives in
`partials/docs_runtime_scripts.html` and re-syncs on `htmx:afterSettle`.

## Directive coverage

All registered Patitas directives follow the directive skin contract:

| Directive(s) | Template | Theme hook |
|--------------|----------|------------|
| note, tip, warning, … | `callout.html` | `chirp-theme-directive-admonition--*` |
| cards / card | `card_grid.html`, `card_link.html` | `chirp-theme-directive-cards`, `chirp-theme-directive-card` |
| child-cards | `child_cards.html` | `chirp-theme-directive-cards--children` |
| tab-set / code-tabs | `tabs.html` | `chirp-theme-directive-tabs` (+ Alpine sync via `furaDocsTabSet`) |
| dropdown | `accordion.html` | `chirp-theme-directive-dropdown` |
| steps / step | `steps.html`, `step.html` | `chirp-theme-directive-steps` |
| since / deprecated / changed | `version_callout.html` | `version-directive`, `chirp-theme-directive-version` |
| related | `related.html` | `chirp-theme-directive-related` |
| list-table | `table.html` | `chirp-theme-directive-table` |
| glossary | `glossary.html` | `chirp-theme-directive-glossary` |
| youtube / gist | `youtube.html`, `gist.html` | `chirp-theme-directive-embed--*` |
| figure | `figure.html` | `chirp-theme-directive-figure` |
| literalinclude | `literalinclude.html` | `chirp-theme-directive-figure--code` |
| Fenced code | `code_block.html` | `code-block-wrapper`, `[data-fura-copy-code]` |

## Directive manifest

`catalog/directives/manifest.py` is the machine-readable registry (`DIRECTIVE_MANIFEST`). It drives:

- `fura check` validation (template files, theme hooks, registry alignment)
- THEMING.md coverage table (keep in sync when adding directives)

When adding a directive: register the handler in `registry.py`, add a manifest entry, Kida template, and directive CSS hook in `theme/directives.css`.

Inline glossary terms use the `{gterm}`\`Term\` role (see `catalog/roles/glossary_term.py`).

## Compose views

Multi-node surfaces (Chirp term: **collection**):

```yaml
# front matter — view kind (alias: kind)
layout: collection
collection: get-started
```

```yaml
# data/collections.yaml
get-started:
  nodes: [docs/get-started, docs/get-started/installation, ...]
```

The collection view stitches member pages inline from the live catalog.

## Commands

```bash
./app/run              # auto: hybrid when freeze is fresh, else author
FURA_MODE=author ./app/run   # force live index
FURA_MODE=preview ./app/run  # frozen only (prod-like)
fura serve                       # same auto resolution as ./run
fura serve --author              # live index
fura serve --preview             # frozen only
fura freeze                      # export catalog + HTML + assets + renderer.fingerprint
fura check                       # shell contract + theme HTML contract (CI)
```

## Serve modes

| Mode | When | Startup | Edits |
|------|------|---------|-------|
| **Hybrid** | Default for `./run` when freeze is current | ~200ms | Watcher overlays dirty markdown |
| **Author** | Stale/missing freeze, or `FURA_MODE=author` | ~2–4s | Full live index + watcher |
| **Preview** | `FURA_MODE=preview` or `--preview` | ~100ms | None (prod-like; `debug=False`) |

Stale detection: content newer than freeze **or** `renderer.fingerprint` mismatch → author mode automatically.

Local `./run` sets `CHIRP_SKIP_CONTRACT_CHECKS=1` so first request is fast; contract checks run in CI via `fura check`. Hashed assets under `/docs-assets/` get long-lived cache headers; local theme files (`/docs-theme/local/`, tokens) use short cache in dev modes.
