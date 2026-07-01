# Furatena views

Furatena is **data-driven**: markdown files become catalog nodes, and **views**
are the page-level templates that decide how each node renders inside the shell.

This document is the canonical reference for views and how they differ from
shell, partials, directives, and front-matter **layout** (view kind).

## Vocabulary

| Term | Role | Where it lives |
|------|------|----------------|
| **Catalog node** | One indexed page — title, slug, body, toc | Loader / `DocNode` |
| **View kind** | Author intent — “what type of page is this?” | Front matter `layout:` or `kind:` |
| **View** | Developer template — full `#page-root` surface | `theme/views/*.html`, `templates/views/*.html` |
| **Shell** | Persistent frame across htmx navigation | `theme/shell.html` |
| **Partial** | Reusable fragment included by views or shell | `partials/*.html` |
| **Directive** | Rich block inside markdown body | `directives/*.html` + `catalog/directives/` |

### Decision tree

```
Renders a whole page inside #page-root?     → view
Persists across boosted navigation?         → shell (or shell partial)
Shared chunk used by multiple views?        → partial
Syntax inside markdown prose?               → directive
```

**Layout is not a view.** Front matter `layout:` (alias `kind:`) is a lightweight
label that **selects** a view. The view template owns page chrome (rail, hero,
TOC, site nav includes).

## Resolution order

`ViewRegistry.resolve()` picks a view template for each catalog node:

1. Front matter **`view:`** or legacy **`template:`** — explicit template path
2. Home URL → **`views/home.html`**
3. **`docs.yaml` `overrides:`** — per-slug template override
4. **View kind** (`node.layout`) → **`docs.yaml` `views:`** map
5. Catalog heuristics — e.g. `layout: doc` + section root with children → **`doc_list`**
6. **`views.default`** fallback

```yaml
# docs.yaml
views:
  doc: views/doc.html
  doc_list: views/doc_list.html
  collection: views/collection.html
  default: views/doc.html

overrides:
  docs/get-started/installation: views/landing.html
```

```markdown
---
title: Installation
layout: doc          # view kind (alias: kind)
view: views/custom.html   # optional — bypasses kind → views map
---
```

## Built-in view kinds

Registered in `catalog/view_kinds.py` (`VIEW_KINDS`):

| Kind | Default view | Surface | Compose | Purpose |
|------|--------------|---------|---------|---------|
| `doc` | `views/doc.html` | catalog | no | Standard doc page |
| `doc_list` | `views/doc_list.html` | catalog | no | Section index (also inferred for section roots) |
| `collection` | `views/collection.html` | catalog | yes | Multi-node read-through |
| `changelog` | `views/changelog.html` | catalog | no | Release notes |
| `api_reference` | `views/api_reference.html` | catalog | no | OpenAPI operation index/detail pages |
| `page` | `views/page.html` | app | no | Simple content page |
| `home` | `views/home.html` | app | no | Site home |
| `portal` | `views/portal.html` | app | no | Federated mount hub |

**Surface** (`chirp_docs_surface`) controls body classes and shell posture:

- **`catalog`** — rail-only docs chrome (`chirp-theme-shell--rail-only`), no site top bar
- **`app`** — marketing / home / portal surfaces (site nav when the view includes it)

## One kind, multiple views

A single view kind can resolve to different templates:

| Trigger | Kind | Resolved view |
|---------|------|---------------|
| Normal doc page | `doc` | `views/doc.html` |
| Section root with children | `doc` | `views/doc_list.html` (heuristic) |
| Slug override | `doc` | `views/landing.html` (explicit) |

Authors set **kind**; developers register **views** and optional **overrides**.

## Compose views

Some views need data beyond a single node. **`ViewRegistry.compose()`** enriches
context before render (today: **`collection`** only).

```yaml
# page front matter
layout: collection
collection: get-started
```

```yaml
# data/collections.yaml (path from docs.yaml compose.collection.data)
get-started:
  title: Get started read-through
  nodes:
    - docs/get-started
    - docs/get-started/installation
```

The collection **view** stitches member bodies inline; the **compose** hook loads
the member list from YAML.

## API Reference Views

OpenAPI autodoc nodes use `layout: api_reference`, which resolves to
`views/api_reference.html`. The view stays on the catalog surface, so it shares
the same shell, rail, htmx navigation, static export, mount context, and theme
tokens as prose docs.

The OpenAPI index node renders the generated operation list. Operation detail
nodes additionally read `node.meta.api_operation` and present method, path,
operation id, request bodies, responses, schemas, examples, auth, environments,
and `externalDocs` links before the generated markdown body.

Operation detail nodes also expose `node.meta.api_try_it`, a static-safe
playground contract. The built-in view presents static, mock, and live mode
availability, tenant/site/mount boundaries, base URL environment references, and
server-only auth token references without exposing token values. Static exports
always degrade to mock examples when available, otherwise render-only static
mode; authenticated live requests require a configured server-side proxy.

## Rendering Heads

Rendering heads are output contracts layered above view kinds. They define how
the same catalog node can be consumed by a live browser shell, static HTML
document, embedded fragment, or future paged/PDF output without forking the
catalog graph.

The built-in head registry is exported through `surface.json`:

| Head | Output | Purpose |
|------|--------|---------|
| `live-shell` | live | Persistent browser shell with boosted navigation, OOB metadata, search, and author/live runtime affordances |
| `static-document` | static | Standalone static HTML with sidecar JSON and static-safe enhancement scripts |
| `embedded-fragment` | embed | Portable body fragment for host applications that cannot assume global shell chrome |
| `paged-output` | pdf | Future paged/PDF output driven by text, sections, links, and directive fallbacks |

Each head declares required catalog fields, assets, navigation assumptions, and
unsupported directives. `fura check` reports head-contract warnings before export
so unsupported embeds or missing catalog fields can be fixed before a head is
used in CI or publishing.

`docs.yaml` can select the default delivery head and theme globally, then override
them per mount:

```yaml
delivery:
  head: live-shell
  theme:
    id: furatena
    use: lagoon
  mounts:
    shared:
      head: embedded-fragment
      theme:
        id: furatena
        use: lagoon
```

Live routes and static exports use the same resolver. Page HTML exposes the
resolved head/theme as `data-fura-rendering-head`, `data-fura-theme-id`, and
`data-fura-theme-use`; `surface.json` exposes the same selections for machine
consumers.

## Folder layout

Template loader stack (**first match wins**):

```
1. templates/              ← project overrides (sparse)
2. theme/templates/        ← theme shadows of framework partials/directives
3. theme/                  ← shell.html, views/, theme partials
4. catalog/_templates/     ← framework defaults (directives, partials, search)
5. chirp-ui                ← component macros
```

| Path | Put here |
|------|----------|
| `theme/views/` | Theme-owned view templates |
| `templates/views/` | Project overrides of a view |
| `theme/partials/` | Theme partials (runtime scripts, head assets) |
| `theme/templates/partials/` | Shadow framework partials (sidebar, toc) |
| `templates/partials/` | Project partial overrides |
| `catalog/_templates/directives/` | Framework directive templates (do not fork in apps) |

## Adding a custom view

1. Create `theme/views/my_view.html` (or `templates/views/my_view.html`)
2. Register in `docs.yaml`:

   ```yaml
   views:
     my_view: views/my_view.html
   ```

3. Assign via front matter or override:

   ```yaml
   overrides:
     docs/demo: views/my_view.html
   ```

   ```markdown
   ---
   view: views/my_view.html
   ---
   ```

4. Extend `shell.html` and fill `#page-root` — include only the chrome this page needs.

5. If the view needs extra catalog data, add a branch in `ViewRegistry.compose()`.

Custom views default to the **app** surface unless their template path matches a
built-in catalog view registration in `docs.yaml`.

## Views vs shell

```
┌ shell.html (persistent) ─────────────────────────────┐
│  #main  [htmx boost → #page-root]                    │
│  ┌ #page-root (view — swaps on navigation) ───────┐  │
│  │  optional site nav · docs rail · hero · body   │  │
│  └────────────────────────────────────────────────┘  │
│  search modal · toasts · runtime scripts             │
└──────────────────────────────────────────────────────┘
```

Do **not** hide view chrome from the shell with CSS. Each view explicitly
includes the partials it needs (e.g. site nav on home only).

### Shared layout partials

| Partial / layout | Used by |
|------------------|---------|
| `layouts/docs_catalog.html` | Block layout for `doc`, `doc_list`, `collection`, `changelog` |
| `layouts/docs_app.html` | Block layout for `home`, `search`, `portal`, `page` |
| `doc_article.html` | Doc body + backlinks inside catalog views |
| `doc_toc.html` | Doc and collection TOC (scroll spy via `toc.js`) |
| `docs_shell_nav.html` | Site top bar (app surface only) |

Layout variables (`catalog_surface`, `app_surface`, etc.) are injected by
`DocsApp._view_chrome_context()` from the resolved view name.

## Kida composition

Furatena templates run on [Kida](https://lbliii.github.io/kida/docs/). Three
mechanisms compose markup — each with different **context scoping**:

| Mechanism | Context | Use in Furatena |
|-----------|---------|-------------------|
| `{% extends %}` / `{% block %}` | Full page render context | Page layouts (`theme/layouts/`) |
| `{% include %}` | Inherits context at the include site | Partials that need `node`, `nav_items`, … |
| `{% def %}` / `{% call %}` / `{% slot %}` | Def params + **slot bodies only** inherit caller context | chirp-ui components (`page_hero`, `card`, …) |

### Rules of thumb

1. **Page chrome → block layouts, not layout macros.** Catalog and app surfaces
   extend `layouts/docs_catalog.html` or `layouts/docs_app.html`. Do not wrap
   context-dependent includes (`docs_sidebar`, `doc_nav`, `doc_article`) inside a
   `{% def %}` body — they will not see `node`, `nav_items`, or `prev_page`.

2. **Slot content inherits caller context; def bodies do not.** From the
   [Kida functions docs](https://lbliii.github.io/kida/docs/syntax/functions/):
   slot bodies filled via `{% call %}` run in the caller's render context. Code
   inside the `{% def %}` itself (including `{% include %}` there) runs in the
   def's isolated scope. Prefer `{% include %}` from views or block layouts.

3. **Use chirp-ui macros for parameterized UI, not page data.** `{% call page_hero(title=...) %}` is correct — explicit args, optional slots. Calling a nested `{% def %}` that reads ambient `node` from inside a slot is not.

4. **Optional variables — use `??` / `?.`, not `is defined`.** Kida raises
   [K-RUN-001](https://lbliii.github.io/kida/docs/errors/) on undefined names.
   Prefer `node?.url ?? ''` over `{% if node is defined %}`.

5. **Slots inside defs are self-closing.** In a `{% def %}` body, write
   `{% slot hero_eyebrow %}` — not `{% slot hero_eyebrow %}{% end %}`. Use
   `{% slot name %}...{% end %}` only on the **caller** side inside `{% call %}`.

6. **htmx fragments → `render_block()`.** Shell and views expose named blocks
   (`page_root`, `page_content`, `catalog_article`, …) for boosted navigation.
   See [Kida framework integration](https://lbliii.github.io/kida/docs/usage/framework-integration/).

### When macros *are* appropriate

Layout macros work when the def is self-contained (explicit parameters) or when
every context-dependent include is placed in **caller-provided slots**:

```kida
{# OK — params only, no ambient page context #}
{% def badge(label, tone="muted") %}
  <span class="badge badge-{{ tone }}">{{ label }}</span>
{% end %}

{# OK — sidebar include lives in slot content from the view #}
{% def chrome(title) %}
  <div class="layout">
    {% slot sidebar %}{% end %}
    <h1>{{ title }}</h1>
    {% slot body %}{% end %}
  </div>
{% end %}

{% call chrome(node.title) %}
  {% slot sidebar %}{% include "partials/docs_sidebar.html" %}{% end %}
  {% slot body %}{% include "partials/doc_article.html" %}{% end %}
{% end %}
```

For Furatena page types, **`{% extends %}` is simpler** — views override blocks
directly without indirection. That matches Kida's
[inheritance docs](https://lbliii.github.io/kida/docs/syntax/inheritance/) and
avoids macro scoping pitfalls entirely.

## Related docs

- [THEMING.md](THEMING.md) — tokens, CSS stack, template loader details
- [README.md](README.md) — run commands and spike overview
- [Kida docs](https://lbliii.github.io/kida/docs/) — syntax, slots, `render_block`, error codes
- `docs.yaml` — shell, views, compose, theme configuration
