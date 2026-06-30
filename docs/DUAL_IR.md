# Dual-IR alignment

Furatena sits between two analyzable intermediate representations:

```
Markdown ──Patitas──► Content IR (structure) ──render──► body_html
                              │
                              └── headings, links, directives

Kida templates ──compile──► Presentation IR (composition) ──render──► views/shell
                              │
                              └── blocks, regions, context contracts
```

The catalog graph (`DocNode`, edges, backlinks, search chunks) is the **runtime glue** between them. Today we throw away the Patitas AST after render and use Kida only as a dumb HTML emitter. Dual-IR alignment means **retaining both trees at index time** and driving graph, validation, and incremental updates from them instead of regex on strings.

See also [ROADMAP.md](ROADMAP.md) for delivery waves, [VIEWS.md](VIEWS.md) for the presentation layer, and [DCP.md](DCP.md) for the format-agnostic catalog protocol.

## Current state (Wave 10 baseline)

| Concern | Before | Wave 10 |
|---------|--------|---------|
| Parse | `Markdown()` → HTML, AST discarded | `parse()` → Content IR → `render(doc)` |
| TOC | Regex on `body_md` | Patitas `Heading` nodes (explicit IDs respected) |
| Link graph | Regex on `body_html` | Content IR links + HTML union (directives still emit HTML links) |
| Directives | Transient at render | Indexed by name + options on `DocNode.content_ir` |
| Export | Metadata only | `content` block in `/catalog.json` |
| Check | `chirp check` (hypermedia contracts) | + broken internal links, view config warnings |

## Target architecture

```
                    ┌─────────────────┐
                    │   DocCatalog    │
                    │  nodes + edges  │
                    └────────┬────────┘
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
      Content IR       body_html      Kida block map
      (Patitas)        (rendered)     (presentation)
              │              │              │
              └──── lint / diff / context_paths ──────┘
                              │
                    incremental invalidation
                    agent queries / CI gates
```

## Waves

### Wave 10 — Content IR (Patitas) ✅ (this pass)

**Goal:** Stop throwing away structure at index time.

- `catalog/content_ir.py` — walk Patitas `Document`, extract headings, links, directives
- `DocsRenderer` — parse once, render from AST
- `DocNode.content_ir` — typed field on every indexed page
- TOC + link edges prefer Content IR
- Export `content` summary in `catalog.json`
- `catalog/check.py` — broken internal link validation
- `fura check` runs content + view config checks after hypermedia check

**Not in scope yet:** full AST JSON persistence, incremental re-parse, Patitas lint rules.

### Wave 11 — Content validation ✅

**Goal:** `fura check` as a corpus linter.

- Content lint rules — heading increment, empty links, empty headings (`catalog/content_lint.py`)
- `DirectiveContract` validation across all pages (steps, tabs, cards nesting)
- Unknown directive name warnings
- Front matter checks — unknown collections, empty `doc_version` (`catalog/frontmatter_lint.py`)
- `ViewRegistry.validate_config()` warnings in check output
- `fura check --content-only` — skip hypermedia check, run content lint only

**Not in scope yet:** Patitas upstream `lint()` (not shipped); full AST JSON persistence (Wave 12).

### Wave 12 — Incremental invalidation ✅

**Goal:** O(change) re-index on author edits.

- AST JSON on `DocNode.ast_json` + freeze sidecars at `frozen/mounts/*/ast/*.json`
- `diff_documents()` + `context_paths_for()` → invalidation regions (`catalog/incremental.py`)
- Selective graph rebuild — body-only edits skip backlink recompute in author mode
- `DocCatalog.invalidation_hints(slug)` → htmx swap targets (`page-root`, `toc-panel`, `head-meta`)
- `catalog.json` records `ast_path` for lazy AST restore in frozen mode

**Not in scope yet:** wire invalidation hints into live htmx author reload (Wave 13+).

### Wave 13 — Presentation IR (Kida) ✅

**Goal:** Run Kida analysis on the docs app like Chirp runs on product apps.

- `catalog/view_lint.py` — `block_metadata()` + `check_context_contract()` (K-CTX-002 errors) per view kind
- `view_kinds.py` — required context/blocks map for catalog + app surfaces
- `check_catalog()` + `fura check --content-only` run view template lint
- `CatalogRegistry.invalidation_hints()` / `clear_invalidation_hints()` / `author_stale_entries()`
- Selective author reload — `_render_author_reload()` uses Wave 12 hints (`page-root`, `toc-panel`, `docs-sidebar`, `head-meta`)
- `GET /docs/_author/events` — SSE invalidation stream for dirty slugs in author/hybrid auto-reload mode
- `GET /docs/_author/stale` — JSON fallback for dirty slugs when SSE is unavailable
- OOB partials — `toc_panel_oob.html`, `docs_sidebar_oob.html`; `#toc-panel` on catalog TOC asides
- Author reload script in `docs_runtime_scripts.html` prefers htmx SSE and falls back to polling when needed

**Not in scope yet:** `extract_literal_attributes()` boosted-link audit (Wave 14); full K-CTX-001 strictness on inherited layouts.

### Wave 14 — Agent-native catalog

**Goal:** Query structure, not grep markdown.

- Graph edges from `Link` + `Directive` nodes (drop HTML regex entirely)
- Agent exports: `directives: [{name, page, line}]`, `headings` index
- `fura query --directive glossary` CLI
- Semantic chunk boundaries from AST sections (replace regex in `chunks.py`)
- Cross-mount link validation with edition/channel awareness

## Design decisions

### Content IR vs full AST JSON

Wave 10 stores a **summary IR** (headings, links, directives) on `DocNode`. Full AST JSON is deferred to Wave 12 freeze targets — smaller in memory, enough for TOC/links/check. Agents that need full trees get them from freeze sidecars.

### HTML link union

Directive handlers emit links only in HTML (glossary entries, child-cards, etc.). Until Wave 14, link edges union Content IR links with HTML extraction. Backlinks stay accurate for directive-generated references.

### Kida fallback

Views stay plain Kida (`{% extends %}`, `{% include %}`). chirp-ui macros are params-only `{% def %}` calls. We do not force `Page.mounted()` + app shell where the theme never wanted persistent chrome. Presentation IR analysis complements that model; it does not replace it.

### Frozen catalog

`catalog.json` v2 gains optional `content` per page. Lazy frozen loads skip re-parse; hybrid overlay re-indexes live markdown with Content IR. Re-freeze after Wave 10 to populate exports.

## Quick reference

| Module | Role |
|--------|------|
| `catalog/content_ir.py` | Patitas AST → Content IR |
| `catalog/check.py` | Corpus validation for `fura check` |
| `catalog/models.py` | `ContentIR`, `DocNode.content_ir` |
| `catalog/render.py` | Parse + render pipeline |
| `catalog/graph.py` | Link extraction (IR + HTML) |
| `DUAL_IR.md` | This plan |
| `VIEWS.md` | View kinds, surfaces, Kida composition rules |

## Experiment that proves the peak

After Wave 10, this should pass locally:

```bash
fura check   # includes broken-link scan
```

Add a page with `[broken](/docs/does-not-exist/)` — check fails with source path and line number from Content IR.

That single feature is awkward in static generators and trivial here because **the catalog and the AST live in the same process**.
