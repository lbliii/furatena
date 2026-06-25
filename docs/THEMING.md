# Furatena theming

Furatena separates **data** (catalog graph), **views** (how nodes render), and **theme** (look-and-feel). There is no build step for production — in dev, edits reload automatically (see below).

**Views architecture:** see [VIEWS.md](VIEWS.md) for view kinds, resolution order,
folder layout, Kida composition rules, and how views differ from shell, partials,
and directives.

## Dev reload

When you run `./app/run` or `fura serve`, the app watches your work and refreshes without a static export loop:

- **CSS** (`theme/tokens.css`, `theme/styles.css`, bundled assets) — browser hot-swap via Chirp dev reload (no full page flash).
- **HTML / Kida templates** — full browser refresh (template env is rebuilt on reload).
- **Markdown** — handled by the docs author pipeline (htmx partial swaps on the open page, not a full reload). See [README.md](README.md#dev-workflow).

Edit CSS or templates and save — you should see changes within a second or two without running `fura export`.

## Four tiers of customization

| Tier | What you change | Where | Effort |
|------|-----------------|-------|--------|
| **1. Tokens** | CSS variables (`--chirpui-accent`, fonts) | `theme/tokens.css` | ~5 min |
| **2. Skin** | Layout spacing, hero, TOC, search chrome | `theme/styles.css` | ~1 hour |
| **3. Templates** | Markup for views, partials, directives | See loader stack below | ~1 day |
| **4. Theme package** | Ship a reusable theme others can `use:` | Python package + `docs.yaml` | days |

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
  id: chirp
  tokens: theme/tokens.css
  styles: theme/styles.css
  templates: theme/templates   # optional shadow dir
```

### What lives where

| Location | Role | Examples |
|----------|------|----------|
| `templates/` | Project overrides | `partials/head_meta.html`, `views/doc.html` |
| `theme/templates/` | Theme overrides of framework | `directives/child_cards.html`, `partials/docs_sidebar.html` |
| `theme/` | Theme-owned surfaces | `shell.html`, `views/doc.html`, `tokens.css` |
| `theme/views/` | Registered view templates | `doc.html`, `home.html`, `collection.html` |
| `templates/views/` | Project view overrides | `views/doc.html` (sparse) |
| `catalog/_templates/` | Framework plumbing (do not edit in apps) | `directives/*`, `partials/*`, `search.html` |

**Override one file without forking:** drop `templates/partials/docs_sidebar.html` in your project, or `theme/templates/partials/docs_sidebar.html` in your theme pack.

## Views vs shell

See [VIEWS.md](VIEWS.md) for the full guide. Summary:

| Concept | File | Purpose |
|---------|------|---------|
| **Shell** | `theme/shell.html` | Persistent frame — `#main`, htmx boost, search modal |
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
2. `/docs-assets/theme.{hash}.css` — bundled chirp-theme skin
3. `/docs-theme/tokens/tokens.css` — your brand variables
4. `/docs-theme/local/styles.css` — project overrides (`@layer docs.overrides`)
5. `/docs-theme/local/directives.css` — directive skin for chirp-ui/Alpine markup (`@layer docs.directives`)

Author mode bundles CSS on first serve (cached in `.docs-cache/`). Freeze copies hashed assets to `frozen/assets/` and writes `renderer.fingerprint` beside the catalog export.

## Directive skin contract

Furatena is self-contained at runtime: theme CSS/icons are vendored under
`theme/assets/`; enhancement scripts live in `theme/js/` (`ChirpDocsTOC`, `ChirpDocsNav`, `ChirpDocsUtils`).
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
htmx:afterSettle → syncDocsChrome() → ChirpDocs.enhance.refresh(#page-root)
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

Add a module with `ChirpDocs.enhance.register(name, { enhance, cleanup })`.

Shell-only glue (search modal, mobile drawer, version select, author poll) lives in
`partials/docs_runtime_scripts.html` and re-syncs on `htmx:afterSettle`.

## Directive coverage

All registered Patitas directives follow the directive skin contract:

| Directive(s) | Template | Theme hook |
|--------------|----------|------------|
| note, tip, warning, … | `callout.html` | `chirp-theme-directive-admonition--*` |
| cards / card | `card_grid.html`, `card_link.html` | `chirp-theme-directive-cards`, `chirp-theme-directive-card` |
| child-cards | `child_cards.html` | `chirp-theme-directive-cards--children` |
| tab-set / code-tabs | `tabs.html` | `chirp-theme-directive-tabs` (+ Alpine sync via `chirpDocsTabSet`) |
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
