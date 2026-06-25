---
title: Freeze and export
description: fura freeze, fura export, and static site output
draft: false
weight: 20
lang: en
type: doc
tags: [freeze, export, static, github-pages]
category: operations
---

**Freeze** writes catalog metadata and rendered HTML shards to `app/frozen/`.
**Export** copies a browsable static site to `app/public/` for GitHub Pages or any static host.

## Freeze

```bash
uv run fura freeze
# parallel writes:
uv run fura freeze --workers 8
```

Output under `app/frozen/`:

| Artifact | Purpose |
|----------|---------|
| `catalog.json` | DCP v3 graph export |
| `semantic.json` | Embedding index for search |
| `structure.json` | Directive + heading index |
| `mounts/*/pages/` | Per-page HTML shards |
| `mounts/*/ast/` | Patitas AST sidecars |
| `assets/` | Bundled CSS fingerprints |
| `inventories/` | `objects.inv` for reference clients |
| `registry.json` | Federated mount registry (v2) |

Freeze validates **`catalog.json`** against the shipped JSON Schema — fails on mismatch.

Serve frozen output without re-indexing:

```bash
fura serve --preview
```

## Export

```bash
uv run fura export
```

Writes static HTML to **`app/public/`** — suitable for GitHub Pages, S3, or any static CDN.

Set the public origin before export:

```bash
FURA_BASE_URL=https://docs.example.com uv run fura export
```

Canonical URLs, Open Graph tags, and sitemap entries use this base.

## GitHub Pages workflow

Furatena is the sole Pages builder (Wave 18 — Bengal path removed). Typical CI:

1. `uv sync --group dev`
2. `FURA_BASE_URL=https://<user>.github.io/<repo> fura freeze`
3. `fura export`
4. Upload `app/public/` as the Pages artifact

See `.github/workflows/pages.yml` in the repository.

## Hybrid local workflow

Many developers keep **`app/frozen/`** in gitignored local cache:

- `fura serve` — hybrid: frozen assets + live markdown index
- `fura freeze` — refresh when preparing a deploy or preview snapshot

Run freeze when content is significantly newer than `frozen/` — the server prints a reminder.

## Next

→ [[docs/operations/check-and-lint|Check and lint]] — validate links, directives, and theme config.
