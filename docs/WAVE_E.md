# Wave E — Federation, inventories, and domain references

Reference inventory resolution and cross-mount graph symmetry — **native
catalog features, zero Bengal dependency**.

Bengal patterns inform config shapes and semantics only (wikilinks, deploy URL
prefixes, autodoc YAML). No `import bengal`, no Bengal build artifacts as runtime
inputs.

See [DCP.md](DCP.md) for the graph protocol, [ROADMAP.md](ROADMAP.md) for prior
waves.

## Goal

Make the catalog a **reference-aware document graph**:

- Cross-mount internal links and backlinks
- External `objects.inv` inventories resolved at index time
- Domain-qualified inline roles (`{py}`, `{xref}`)
- CI validation for unresolved references
- Configurable URL rewrites (legacy deploy prefixes)

## Architecture

```
inventories.yaml ──► InventoryStore ──┐
mounts.yaml ───────► CatalogRegistry ├──► ReferenceResolver ──► graph + check
url_rewrites.yaml ─► RewriteTable ───┘
```

Author surfaces:

- `[[mount:slug|label]]` — mount-qualified wikilinks
- `{xref}`mount:slug`` — explicit internal xref role
- `{py}`os.path.join`` — inventory domain role
- `[text](/docs/foo/)` — unchanged markdown links

## E1 — URL rewrite table

**Today:** hardcoded `/chirp/docs/` → `/docs/` in `catalog/directives/html.py`.

**Deliver:**

- `data/url_rewrites.yaml` (or `docs.yaml` `rewrites:`):

  ```yaml
  prefixes:
    - from: /chirp/docs/
      to: /docs/
    - from: /chirp/
      to: /
  ```

- `catalog/rewrites.py` — longest-prefix match loader
- `rewrite_href()` delegates to config; defaults preserve current behavior

## E2 — Cross-mount graph and backlinks

**Today:** `build_backlinks()` only links within same mount + edition.

**Deliver:**

- `CatalogRegistry` — federated backlink map over global `(edition, url)` index
- Mount-qualified wikilinks in `catalog/sources/scanner.py`:

  ```markdown
  [[chirp:docs/get-started|Quickstart]]
  [[shared:reference/foo|Shared]]
  ```

- Content IR `links[]` optional `mount` field (DCP v3.1)

## E3 — Reference inventory store

**Deliver:**

- `inventories.yaml` at app root:

  ```yaml
  inventories:
    - id: python-stdlib
      format: sphinx-inventory
      url: https://docs.python.org/3/objects.inv
      cache: .docs-cache/inventories/python-stdlib.inv

    - id: chirp-api
      format: dcp-catalog
      mount: chirp
      domain: py
      slug_prefix: api/
  ```

- `catalog/inventories/`:
  - `sphinx.py` — parse `objects.inv` (stdlib zlib/pickle; no Sphinx package)
  - `dcp.py` — inventory from live/frozen autodoc + API slugs
  - `store.py` — `InventoryStore` with file cache; remote fetch via stdlib `urllib`

## E4 — Domain roles and reference resolver

**Deliver:**

- `catalog/references/resolver.py`:

  ```text
  py:os.path.join     → external URL (inventory)
  chirp:docs/get-started → internal node_id + url
  doc:path/to/page    → catalog slug lookup
  ```

- Patitas roles: `xref`, `py` (and extensible domain → inventory map)
- Unresolved refs render as `<span class="xref-unresolved">`; check errors on them
- RST adapter maps `:py:func:` and `:doc:` to same resolver

Config:

```yaml
role_domains:
  py: python-stdlib
  doc: local-catalog
```

## E5 — Validation, export, proof corpus

**Deliver:**

- `check_unresolved_references()` alongside broken-link check
- `catalog.json` — `inventories[]` metadata; link records with `domain`, `inventory_id`, `resolved`
- Proof page: `content/shared/reference/reference-inventories.md`
- Tests: `tests/test_chirp_docs_reference_resolution.py`
- ROADMAP Wave E section marked planned → done as sub-waves land

## DCP v3.1 (minor bump)

| Field | Purpose |
|-------|---------|
| `links[].mount` | Cross-mount hint |
| `links[].domain` | `py`, `std`, `doc` |
| `links[].inventory_id` | Source inventory |
| `links[].resolved` | Check/export |
| `inventories[]` | Top-level loaded inventory metadata |

Backward compatible — v3 consumers ignore new fields.

## Bengal → catalog (no dependency)

| Bengal-era pattern | Wave E implementation |
|--------------------|----------------------|
| `baseurl` / `/chirp/docs/` | `url_rewrites.yaml` |
| Wikilinks | mount-qualified scanner |
| Autodoc | `dcp-catalog` inventory slice |
| Reference inventory mapping | `inventories.yaml` + `.inv` parser |
| Domain roles | Patitas roles + resolver |
| Related content | cross-mount backlinks + semantic (Wave 9) |

## Out of scope (Wave E+)

- Full RST extension surface
- Publishing our own `objects.inv` for third parties
- Edition-aware xref filtering (`chirp:1.0:…` vs `latest`)
- MDX → canonical directive codemod (`fura migrate` — Wave F)

## Success criteria

1. Cross-mount link from `shared` → `chirp` appears in target backlinks
2. `{py}`role`` resolves from cached `objects.inv`
3. `fura check` fails on broken `{xref}` with path + line
4. `/chirp/docs/` rewrites via YAML, not hardcoded strings
5. Zero Bengal in `pyproject.toml`
6. Tests use local fixtures; remote `.inv` optional in CI

## Implementation order

1. E1 — rewrites
2. E2 — cross-mount backlinks + wikilinks
3. E3 — inventory store
4. E4 — roles + resolver
5. E5 — check, export, proof, docs
