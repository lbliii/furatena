---
title: Deploy
owner: security-operations
reviewed_at: "2026-07-07"
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

For a first project-site deployment with a complete GitHub Actions workflow and
recovery steps, follow [[docs/get-started/first-github-pages-deploy|First GitHub Pages deploy]].

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
| `/channels.json` | Publication channel manifest for live, static, agent, and PDF outputs |
| `/deployment-profiles.json` | Local, static, cloud, and self-hosted deployment profiles |
| `/llms.txt` | Page index for LLMs |
| `/sitemap.xml` | SEO sitemap |
| `/objects.inv` | Sphinx-compatible inventory |

Set **`FURA_BASE_URL`** so these URLs resolve correctly in exported JSON.

`channels.json` records the active docs channel, site identity, public/protected page
counts, source/catalog/theme fingerprints, canonical URLs, and output artifacts. It
uses deployment manifest schema v3, the same `furatena.deployment` envelope used by
`freeze.manifest.json`, `export.manifest.json`, and PDF `manifest.json`: inspect
`target`, `mode`, `artifacts`, `fingerprints`, and `sync` before target-specific
fields. PDF is advertised as `planned` until PDF artifacts exist, then the PDF
channel lists the generated files.

## PDF artifacts

Use `fura pdf` to publish a page, collection, or full-site PDF bundle:

```bash
uv run fura pdf --page /docs/get-started/
uv run fura pdf --collection docs
uv run fura pdf
```

By default the command writes to `app/public/pdf/`, writes a PDF `manifest.json`,
and refreshes `app/public/channels.json` so deploy tooling can discover generated
PDF outputs. Pass an explicit output directory for a custom artifact location, or
`--no-channels` when a job should not update the public channel manifest.

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
