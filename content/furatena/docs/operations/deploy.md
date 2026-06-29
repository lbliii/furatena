---
title: Deploy
description: FURA_BASE_URL, version channels, and production configuration
draft: false
weight: 40
lang: en
type: doc
tags: [deploy, github-pages, seo, channels]
category: operations
---

Deploy Furatena as a live Chirp app or as a static export. Set public URLs and channels
before freeze/export so SEO metadata is correct.

## Environment variables

| Variable | Purpose |
|----------|---------|
| **`FURA_BASE_URL`** | Public origin for canonical, OG, sitemap, and JSON exports |
| **`FURA_CHANNEL`** | Active release channel (`latest` or a release id) |
| **`FURA_WORKERS`** | Parallel indexing pool size (default `min(cpu, 8)`) |
| **`FURA_FROZEN=1`** | Legacy alias for preview mode |
| **`FURA_MODE=author`** | Legacy alias for author mode |

Example:

```bash
export FURA_BASE_URL=https://lbliii.github.io/furatena
export FURA_CHANNEL=latest
uv run fura freeze --workers 8
uv run fura export
```

## URL rewrites

Legacy deploy prefixes map to app routes via **`data/url_rewrites.yaml`**:

```yaml
prefixes:
  - from: /furatena/docs/
    to: /docs/
  - from: /furatena/
    to: /
```

Useful when GitHub Pages serves under a subpath but local dev uses root paths.

## Federated deploys

Multi-mount sites export a **`registry.json`** with per-mount shards under
`frozen/mounts/<id>/`. The default mount serves `/`; secondary mounts use `url_prefix`
(for example `/shared/`).

## Machine-readable endpoints

Agents and tooling can consume live or frozen exports:

| URL | Format |
|-----|--------|
| `/catalog.json` | DCP v3 graph |
| `/search.json` | Keyword search index |
| `/semantic.json` (frozen) | Embedding vectors |
| `/tools.json` | Agent tool manifest |
| `/llms.txt` | Page index for LLMs |
| `/sitemap.xml` | SEO sitemap |
| `/objects.inv` | Sphinx-compatible inventory |

Set **`FURA_BASE_URL`** so these URLs resolve correctly in exported JSON.

## Branding at deploy time

Product name, nav copy, and home hero come from **`site:`** in `app/docs.yaml` — no template
edits required for a rebrand.

Branding assets (favicon, web manifest) live in **`app/theme/assets/branding/`**.

## Static vs live

| Approach | Best for |
|----------|----------|
| **Static export** (`fura export`) | GitHub Pages, CDN, immutable hosting |
| **Live Chirp app** (`fura serve` behind Pounce) | Author mode, dynamic search, always-fresh index |

This repository dogfoods both: CI exports static Pages; local dev runs author mode.

## Next

→ [[docs/reference/docs-yaml|docs.yaml reference]] — full app configuration.
