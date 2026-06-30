# Furatena App

Hypermedia documentation spike: markdown from `content/chirp/` indexed at
startup into a `DocCatalog`, served through Chirp with a persistent chirp-ui
shell and boosted htmx navigation.

## Run

From the repo root (first time: `make install`):

```bash
./app/run
```

Or explicitly with uv:

```bash
export PYTHONPATH=src
uv run --extra markdown --group docs python app/app.py
```

Open http://127.0.0.1:8001/ (default port; override with `FURA_PORT=8002`).

## Dev workflow

The docs app uses **three reload layers** — no static rebuild, no Lunr regen.

| You edit | What happens | Typical wait |
|----------|----------------|--------------|
| Markdown in `content/chirp/` | Current page updates in place (htmx OOB: body, TOC, sidebar) | ~2–3s |
| Theme CSS (`theme/…`) | Stylesheets hot-swap in the browser | ~0.5s |
| Theme HTML / Kida templates | Full browser refresh | ~0.5s |
| Python (`catalog/…`, optional `src/chirp/`) | Dev server process restart | ~3–5s |

**Serve modes** (auto-selected unless you pass a flag):

| Mode | Boot | Live edits |
|------|------|------------|
| `hybrid` | Fast — loads `frozen/` catalog | Content overlay + all reload layers |
| `author` | Full live index from source | Same |
| `preview` | Frozen only, prod-like | None |

```bash
./app/run                 # hybrid when frozen/ is current (default)
fura serve --hybrid                 # frozen baseline + live overlay
fura serve --author                 # always live index (slowest boot, always fresh)
fura serve --preview                # no live reload

# Optional: also restart on edits under src/chirp/ (framework co-dev)
FURA_RELOAD_SRC=1 ./app/run
```

Startup prints the active mode and reload layers. If content is newer than `frozen/`, the server switches to author mode and suggests `fura freeze`.

Theme-specific behavior: see [THEMING.md](THEMING.md#dev-reload).

## Static export (GitHub Pages eval)

Compile (freeze) then link into static HTML:

```bash
fura freeze                    # catalog IR + body HTML + assets
fura export                    # writes app/public/
make docs-export                     # same via ./app/export
make docs-preview                    # serve under /chirp/ like GitHub Pages
make docs-pages-build                # freeze + export (CI / upload)
fura export --incremental      # skip unchanged pages (~1s)
fura query --directive tabs      # Wave 14 content IR query
```

Defaults target GitHub Pages project-site layout (`--base-path /chirp`,
`--site-url https://lbliii.github.io/chirp`). For a flat local preview:

```bash
fura export /tmp/public --base-path '' --site-url http://127.0.0.1:8080
cd /tmp/public && python -m http.server 8080
```

**Do not use bare `python app/app.py`** unless your active
environment already has `chirp-ui`, `patitas`, and `pyyaml` installed — the
repo venv from `make install` does.

## What this demonstrates

- **Docs as data** — 122 markdown files indexed in memory, no static-site build
- **Persistent shell** — sidebar and topbar stay mounted during navigation
- **Boosted nav** — htmx swaps `#main` (~7KB fragments, ~10ms server time)
- **Shell OOB updates** — sidebar active state, breadcrumbs, and title update per navigation
- **Runtime search** — query the catalog, no prebuilt Lunr index
- **`/llms.txt`** — machine-readable index generated on request, with API operation hints when available

## Limitations (spike)

- Patitas directives render through **Kida + chirp-ui macros** at catalog index time (`catalog/directives/`, `templates/directives/`)
- Supported: admonitions, cards, child-cards, dropdown, tabs, code-tabs (Rosettes), steps, since/deprecated/related, list-table, include, literalinclude
- **`GET /catalog.json`**, **`GET /sitemap.xml`**, enriched **`/llms.txt`**
- **Search v2** — snippets, heading boost, sidebar **`/search/suggest`** (htmx)
- **SEO** — per-page title, meta description, Open Graph (with OOB updates on boosted nav)
- **Glossary, youtube, gist, figure** directives; data in `data/glossary.yaml`
- **Production fast start:** `FURA_FROZEN=1 ./app/run` after `./app/freeze`
- **Wave 6:** `/search.json`, `/tools.json`, `/catalog/api-operations.json`, Python/OpenAPI autodoc (`/api/…`), `fura serve|freeze|check|export`
- **Wave 7:** incremental reindex, directive template registry for `chirp check`, JSON-LD + OG images + `FURA_BASE_URL`
- **Wave 8:** federated `mounts.yaml`, `/portal/`, `/catalog.json` v2 edges + namespaces, lazy frozen HTML
- **Wave 9:** `/search/semantic`, `/catalog/retrieve`, `semantic.json` chunk index at freeze
- **Views & theme:** [VIEWS.md](VIEWS.md) (views, kinds, partials) · [THEMING.md](THEMING.md) (tokens, CSS)
- **Dual-IR plan:** [DUAL_IR.md](DUAL_IR.md) (Patitas content IR + Kida presentation IR)
- See [ROADMAP.md](ROADMAP.md) for the full roadmap
- Wikilinks are converted to internal links; external `/chirp/` prefixes remain
