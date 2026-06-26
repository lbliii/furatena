# Furatena roadmap

Hypermedia docs: markdown → `DocCatalog` → Kida/chirp-ui HTML → htmx fragments.

## Done

| Wave | Theme | Delivered |
|------|--------|-----------|
| **1** | Native directives | Patitas handlers, chirp-ui HTML for admonitions, cards, tabs, steps |
| **2** | Kida + graph | `templates/directives/`, list-table, include, child-cards, backlinks |
| **3** | Platform hooks | literalinclude, Rosettes code-tabs, auto-reload, `/catalog.json`, `freeze` |
| **4** | Discoverability | SEO meta/OG, search v2 + snippets, `/search/suggest`, `/sitemap.xml`, richer `/llms.txt` |
| **5** | Content parity | glossary, youtube/gist/figure, `DocCatalog.from_frozen()`, `data/glossary.yaml` |
| **6** | Product surface | `/search.json`, `/tools.json`, Python autodoc slice, version channels, `fura` CLI |
| **7** | Production hardening | dirty-node reindex, directive template registry for `chirp check`, JSON-LD + OG images + deploy canonical URLs |
| **8** | Graph platform | `(mount, edition)` node ids, `CatalogRegistry`, typed edges in `/catalog.json` v2, lazy frozen HTML, `/portal/` |
| **9** | Intelligence layer | chunk index, hybrid `/search/semantic`, `/catalog/retrieve`, `semantic.json` at freeze |

## Wave 8 — Graph platform ✅

- **`DocNode` namespaces** — `mount`, `edition`, `node_id`, `section_root`
- **`CatalogShard` / `CatalogRegistry`** — federated mounts via `mounts.yaml`
- **Typed edges** — `parent`, `link`, `nav_next`, `nav_prev`, `tag` in `/catalog.json`
- **Lazy HTML** — `FURA_FROZEN=1` loads metadata; HTML read from `frozen/mounts/*/pages/`
- **Federated freeze** — `registry.json` + per-mount shards under `frozen/mounts/`
- **Portal** — `/portal/` lists mounts (Chirp + Shared Reference)

## Wave 9 — Intelligence layer ✅

- **Chunks** — page summary + heading sections with stable `chunk_id`
- **Embedding index** — TF-IDF cosine similarity (`semantic.json`, no ML deps)
- **`GET /search/semantic?q=`** — hybrid keyword + semantic rank
- **`GET /catalog/retrieve?id=`** — node + chunks + backlinks + similar pages
- **Agent tools** — `semantic_search`, `retrieve_doc` in `/tools.json`

## Wave 10 — Dual-IR (Content IR) ✅

Patitas AST captured at index time; graph and validation read structure instead of regex.

- **`ContentIR`** on `DocNode` — headings, links, directives from Patitas parse
- **Parse once, render twice** — `DocsRenderer.parse()` + `render(doc)`
- **TOC from AST** — explicit heading IDs respected
- **Link graph** — Content IR + HTML union for directive-generated links
- **`/catalog.json` `content` block** — exported summary for agents/freeze
- **`fura check`** — broken internal links + view config warnings
- **Plan** — [DUAL_IR.md](DUAL_IR.md) (Waves 11–14: validation, incremental, Kida IR, agent queries)

## Wave 11 — Content validation ✅

- **`catalog/content_lint.py`** — heading increment, empty links/headings, directive contracts, unknown directives
- **`catalog/frontmatter_lint.py`** — collection references, `doc_version`, custom view kinds
- **`fura check --content-only`** — corpus lint without hypermedia app.check
- Integrated into `check_catalog()` alongside broken-link validation (Wave 10)

```bash
fura check --content-only   # heading/directive/link lint only
fura check                  # hypermedia + content lint
```

## Wave 12 — Incremental invalidation ✅

- **`catalog/incremental.py`** — AST diff → invalidation regions + htmx swap hints
- **`catalog/ast_store.py`** — Patitas AST JSON round-trip
- **`DocNode.ast_json`** — persisted at index; freeze sidecars under `mounts/*/ast/`
- **Selective reindex** — body-only edits skip full backlink graph rebuild
- **`DocCatalog.invalidation_hints(slug)`** — `page-root`, `toc-panel`, `head-meta`

## Wave 13 — Presentation IR (Kida) ✅

- **`catalog/view_lint.py`** — Kida block/context checks on registered view templates
- **`view_kinds.py`** — required context + blocks per view kind
- **Author selective reload** — hints drive OOB swaps (`toc-panel`, `docs-sidebar`, `head-meta`, `page-root`)
- **`GET /docs/_author/stale`** — poll dirty slugs during auto-reload; runtime JS triggers htmx refresh
- **`fura check --content-only`** — includes view template lint alongside content validation

## Try it

```bash
./app/run              # hybrid when frozen/ exists (default)
./app/freeze           # export catalog + bundled assets
fura serve --author              # force live index
fura serve --preview             # frozen only, prod-like

# Legacy env vars still work:
FURA_FROZEN=1 ./app/run
FURA_MODE=author ./app/run
```

Deploy canonical URLs: set `FURA_BASE_URL=https://docs.example.com` before serve/freeze.

Version channel: `FURA_CHANNEL=latest` (default) or a release id from `releases/*.md`.

Example glossary in markdown:

```markdown
:::{glossary}
:tags: hypermedia, chirp
:collapsed: true
:::
```

## Wave E — Federation and references ✅

Reference inventories, cross-mount backlinks, and domain roles — native catalog, no Bengal dependency.

- **`url_rewrites.yaml`** — configurable legacy deploy prefix rewrites (`/chirp/docs/` → `/docs/`)
- **`inventories.yaml`** — external `objects.inv` inventories + catalog-backed inventories
- **`InventoryStore`** — domain role resolution (`{py}`, `{xref}`, `{doc}`)
- **Cross-mount graph** — federated backlinks and `[[mount:slug|label]]` wikilinks
- **`fura check`** — unresolved reference validation alongside broken links
- **`/catalog.json`** — top-level `inventories[]` metadata export
- **Plan** — [WAVE_E.md](WAVE_E.md)

## Wave F — MDX migration ✅

Lower JSX-heavy MDX into canonical Patitas markdown for long-term authoring.

- **`mdx_to_markdown()`** — shared lowering used by the MDX adapter at index time
- **`fura migrate`** — scan mount content roots (or explicit paths), write `.md` siblings
- **`--dry-run`** / **`--keep-mdx`** — preview or retain source files during migration
- Unmigrated capitalized JSX tags reported as warnings for manual follow-up

## Wave 14 — Agent-native catalog ✅

AST-driven link graph and structure indexes for agents and CI.

- **Content IR link edges** — `child-cards`, `related`, and card `link` options (no HTML regex)
- **`structure.json` at freeze** — flat directive + heading indexes with source lines
- **`/catalog.json` `structure_index`** — summary counts and directive name roll-up
- **`fura query`** — `--mount`, `--edition`, `--tag`, `--url-prefix` filters
- **Freeze federation** — `registry.json` v2 carries inventory metadata; rewrites/inventories loaded at freeze

## Wave 15 — References and retrieval hardening ✅

Close the gap between federation, inventories, and semantic search.

- **Edition-aware xrefs** — `{xref}`chirp:latest:docs/foo`` and `latest:docs/foo` resolution
- **`{doc}` inventory** — local-catalog includes doc slugs alongside autodoc API entries
- **`objects.inv` export** — `frozen/inventories/*.inv` written at freeze for third-party inventory clients
- **AST section chunks** — semantic chunks fall back to Content IR headings when `node.sections` is empty
- **Cross-edition link warnings** — `fura check` warns when editions mismatch on internal links
- **Dead HTML link scraper removed** — graph uses Content IR exclusively

## Wave 16 — Link audit and inventory HTTP ✅

Production-hardening for htmx navigation and reference-inventory consumers.

- **Boosted-link audit** — `check_body_link_boost()` verifies internal links get `hx-boost` via `boost_doc_links`
- **Directive template audit** — static internal `href` in directive partials flagged when missing `hx-boost`
- **`GET /objects.inv`** and **`GET /inventories/{id}/objects.inv`** — live or frozen `objects.inv` inventories
- **`GET /inventories.json`** — reference inventory index for external mapping
- **Strict cross-edition links** — `--strict-edition-links` / `--deploy` promote edition mismatches to errors
- **`/tools.json`** — `objects_inv_url` and `inventories_url` for agents

## Wave 17 — Parallel indexing ✅

Free-threading-friendly catalog build using thread pools (default `min(cpu, 8)`).

- **`catalog/workers.py`** — `resolve_workers()` from `FURA_WORKERS` / `--workers`
- **Parallel page adapt** — per-thread `DocsRenderer` during `DocCatalog._load()`
- **Parallel mount shards** — `CatalogRegistry._load_shards()` builds live mounts concurrently
- **Parallel autodoc modules** — `generate_autodoc_nodes()` thread pool for API pages
- **Parallel freeze writes** — shard HTML/AST export uses worker pool
- **CLI** — `fura serve --workers N`, `fura freeze --workers N`
- **DCP schema** — `schemas/catalog-v3.schema.json` for external `catalog.json` consumers
- **Mixed-format proof** — `content/shared/formats/html-bridge.html` in the shared mount

## Wave 18 — Bengal cutover ✅

Furatena is the sole GitHub Pages builder.

- **`.github/workflows/pages.yml`** — Chirp freeze+export only; Bengal path removed
- **CI** — parallel freeze via `FURA_WORKERS=8` in Pages build
- **Makefile** — `site-serve` documented as legacy Bengal path

## Wave 19 — Catalog package layout ✅

The document runtime is an installable package (standalone-repo ready).

- **`src/furatena/catalog/`** — moved from `app/catalog/`
- **`furatena` namespace** — installable runtime package; `fura` remains the CLI entrypoint
- **Paths** — `catalog.paths.catalog_root()` for reload fingerprints (not `docs_root/catalog`)
- **`app/`** — app config, theme, content mounts, freeze scripts only

## Wave 20 — DCP contract ✅

`catalog.json` v3 is validated, not just documented.

- **`catalog/dcp_validate.py`** — JSON Schema validation via `jsonschema`
- **`catalog/schemas/catalog-v3.schema.json`** — ships with the runtime package
- **`fura check`** — validates live graph export against DCP v3
- **`fura freeze`** — fails when merged `catalog.json` does not match schema
- **Docs page** — `content/chirp/docs/about/document-catalog-protocol.md`

## Wave 21 — Theme consolidation ✅

Native Fura theming ergonomics on top of chirp-ui + packaged docs-core CSS.

- **Wave 21a ✅** — Effect presets (`theme.effects`), `effects.css`, bundle inventory
- **Wave 21b ✅** — Drop 8 superseded bundle imports; split `theme/skin/*`; `fura check` theme lint
- **Wave 21c ✅** — Declarative `measure`/`fonts` in `docs.yaml` → generated `theme-preset.css`; drop 8 more bundle modules
- **Wave 21d ✅** — `furatena.themes` entry points; built-in `lagoon` skin pack; `theme.use` + overrides

## Wave 22 — Docs-core package ✅

Two-tier theming: installable **docs-core** (`theme.id`) + reusable **skin pack** (`theme.use`).

- **Wave 22a ✅** — Move vendored docs-core CSS/icons to `src/furatena/themes/chirp/`; app keeps branding/images
- **Wave 22b ✅** — `load_docs_core()` + updated asset resolution, fingerprint, and theme lint
- **Wave 22c ✅** — Native `layouts/fura_shell.html`; app shell no longer extends `chirp/layouts/shell.html`

See [THEMING.md](THEMING.md) for the tier model and preset reference.

## Wave 23 — App maturity ✅

Production polish for shell assets, navigation, and error UX.

- **Vendored htmx** — `furatena.catalog/vendor/` served at `/docs-vendor/` (no unpkg CDN)
- **`GET /favicon.ico`** — serves branding icon for default browser requests
- **App nav** — Portal removed from top bar; Shared reference linked under Develop
- **Error pages** — status-aware templates with path display, live `/errors/suggest` fragment, boosted OOB meta
- **403/405/413 handlers** — forbidden, method-not-allowed, and payload-too-large pages share the app shell
- **Semantic recovery** — 404 splits keyword “Did you mean?” from semantic “Related pages”
- **Error overrides** — shadow `theme/templates/error.html` (documented in `THEMING.md`)
- **Shell CSS** — app-surface header flush to viewport top
- **`fura check`** — validates vendor assets and warns on missing `favicon.ico`

## Wave 24 — Landing & marketing surface ✅

Polish the app home page and mobile site navigation.

- **`skin/home.css`** — lagoon landing hero, product visual, and proof metrics
- **Mobile site nav** — drawer menu for Documentation + Develop on small screens
- **Develop index** — `/develop/` lists machine-readable exports with HTML previews
- **Contract tests** — home + develop preview routes (`tests/test_chirp_docs_landing.py`)

## Wave 25 — Docs-core bundle shrink ✅

Further trim unused Bengal modules from the packaged bundle.

- **Removed imports** — `link-preview.css`, `empty-state.css` (files remain on disk)
- **`BUNDLE_INVENTORY.md`** — fixed duplicate Active/Dead drift for retired modules

## Wave 26 — Freeze & preview parity ✅

Frozen catalog serves the same runtime assets as author mode.

- **Vendor in freeze** — `htmx` + SSE extension copied to `frozen/assets/vendor/`
- **Manifest** — `vendor_prefix` in `assets/manifest.json`
- **Preview mounts** — `/docs-vendor/*` served from frozen assets when manifest present

## Wave 27 — Develop export UX ✅

Human-readable previews for agent-facing exports; raw URLs stay canonical.

- **`/develop/`** — export catalog with descriptions
- **`/develop/{id}/`** — truncated sample + download link to raw `/llms.txt`, `/catalog.json`, etc.
- **Develop mega-menu** — links to HTML previews; raw endpoints unchanged

## Wave 28 — Theme package story ✅

Scaffold reusable skin packs without forking lagoon.

- **`fura theme init [DIR]`** — writes tokens, styles, skin/*, branding README
- **THEMING.md** — documents init workflow and override wiring

## Wave 29 — Hygiene ✅

Rename runtime JS globals to match the Furatena product name.

- **`FuraDocs*`** — `FuraDocs`, `FuraDocsUtils`, `FuraDocsNav`, `FuraDocsTOC`, `FuraDocsTheme`, `furaDocsTabSet`
- **Export branding** — `llms.txt`, `llms-full.txt`, and `tools.json` use `site.name` from config
- **Runtime bind flags** — internal `dataset` / poll globals renamed from `chirp*` → `fura*`
