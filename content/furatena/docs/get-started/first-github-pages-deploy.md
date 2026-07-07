---
title: First GitHub Pages deploy
description: Build, verify, and publish a correctly based Furatena site with GitHub Actions.
weight: 35
lang: en
type: doc
tags: [github-pages, deploy, base-url, base-path, troubleshooting]
category: onboarding
---

# First GitHub Pages deploy

This tutorial takes a Furatena app in `app/` to a verified GitHub Pages project
site at `https://OWNER.github.io/REPOSITORY/`. If your app lives at the repository
root, change `FURA_APP_ROOT` to `.` and use `frozen/` and `public/` below.

## 1. Verify the source locally

Use free-threaded Python for the same runtime posture as CI:

```bash
export PYTHON_GIL=0
export FURA_APP_ROOT=app
uv run fura check --content-only --warnings-as-errors
```

Fix source diagnostics before building. A successful source check does not prove
that public URLs are based correctly, so the artifact gets a second verification
after export.

## 2. Set the public URL contract

Replace `OWNER` and `REPOSITORY` once in each value:

```bash
export FURA_BASE_URL="https://OWNER.github.io/REPOSITORY"
export FURA_BASE_PATH="/REPOSITORY"
```

`FURA_BASE_URL` is the canonical public origin, including the Pages project path.
`FURA_BASE_PATH` is the path prefix used by links and assets. For a user/organization
site at `https://OWNER.github.io/`, use an empty base path and that root URL instead.

## 3. Build from clean output directories

```bash
rm -rf app/frozen app/public
uv run fura freeze app/frozen
uv run fura export app/public \
  --frozen app/frozen \
  --base-url "$FURA_BASE_URL" \
  --base-path "$FURA_BASE_PATH"
```

The export includes `.nojekyll`, HTML, hashed assets, search/catalog/agent sidecars,
`sitemap.xml`, and `channels.json`.

## 4. Crawl the exact production artifact

```bash
uv run python -m furatena.catalog.artifact_audit app/public \
  --site-url "$FURA_BASE_URL" \
  --base-path "$FURA_BASE_PATH"
```

The crawler follows HTML, JSON, XML, and navigable text references. It reports the
source artifact and public referrer for missing targets, escaped project paths,
repeated project paths, and incorrect canonical origins. This is the reliable local
verification step for a subpath deployment.

## 5. Add the Pages workflow

Create `.github/workflows/pages.yml`:

```yaml
name: Deploy docs

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

env:
  PYTHON_GIL: "0"
  FURA_APP_ROOT: app
  FURA_BASE_URL: https://${{ github.repository_owner }}.github.io/${{ github.event.repository.name }}
  FURA_BASE_PATH: /${{ github.event.repository.name }}

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7.0.0
      - uses: astral-sh/setup-uv@v8.2.0
        with:
          python-version: "3.14t"
      - run: uv sync
      - run: uv run fura check --content-only --warnings-as-errors
      - run: rm -rf app/frozen app/public
      - run: uv run fura freeze app/frozen
      - run: >-
          uv run fura export app/public
          --frozen app/frozen
          --base-url "$FURA_BASE_URL"
          --base-path "$FURA_BASE_PATH"
      - run: >-
          uv run python -m furatena.catalog.artifact_audit app/public
          --site-url "$FURA_BASE_URL"
          --base-path "$FURA_BASE_PATH"
      - uses: actions/upload-pages-artifact@v3
        with:
          path: app/public

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Deploy
        id: deployment
        uses: actions/deploy-pages@v4
```

In repository **Settings → Pages**, choose **GitHub Actions** as the source. Push
to `main`, wait for both jobs, then open the environment URL from the deploy job.

## Troubleshooting

### A link contains `/REPOSITORY/REPOSITORY/`

This is a doubled prefix. Keep the repository name once in `FURA_BASE_URL` and
once in `FURA_BASE_PATH`; do not append the base path to command arguments or
content links. Rebuild from clean directories and rerun `artifact_audit`. Its
`repeated-base-path` finding names the offending file and link.

### The deployed site shows old pages or assets

Delete both `app/frozen/` and `app/public/` before rebuilding. A stale frozen graph
can preserve old content; a stale public directory can preserve removed routes or
hashed assets. The workflow above always cleans both. Confirm the Pages run uploaded
the artifact from the current commit rather than a previous successful run.

### The crawler reports `missing-target` or `escaped-base-path`

Run `fura check --content-only --warnings-as-errors` first to repair source links.
Then use the crawler's source/referrer pair to find generated-only failures. Root
links must resolve beneath `/REPOSITORY/`; links that intentionally leave the site
must use a complete `https://` URL. Rebuild and rerun the crawler before pushing.

### Canonical URLs point at localhost or the wrong repository

Set `FURA_BASE_URL` before freeze/export and pass the same value to the artifact
audit. Clear stale output and rebuild; canonical, Open Graph, sitemap, and JSON
URLs are generated from that value.

## Next

Read [Deployment profiles](/docs/operations/deployment-profiles/) before moving
from static Pages to a live or self-hosted deployment.
