---
title: Get Started
description: Install Furatena and run your first hypermedia documentation site
draft: false
weight: 1
lang: en
type: doc
tags: [getting-started, installation, quickstart]
keywords: [install, setup, quickstart, fura, documentation]
category: onboarding
icon: rocket

cascade:
  type: doc
---

Welcome to Furatena. This section takes you from zero to a running docs site with live
reload — markdown in `content/`, hypermedia out in the browser.

## Prerequisites

- **Python 3.14+**
- **[uv](https://docs.astral.sh/uv/)** (recommended) or pip
- Basic familiarity with markdown and HTTP

## Learning path

1. [[docs/get-started/installation|Installation]] — clone, sync dependencies
2. [[docs/get-started/quickstart|Quickstart]] — `fura serve` and your first edit
3. [[docs/get-started/project-layout|Project layout]] — `app/`, `content/`, `src/furatena/`

## What you'll have running

- A home page at `/` rendered from `content/furatena/_index.md`
- A docs tree under `/docs/`
- Search, theme switching, and htmx boosted navigation
- Optional [Chirp docs mount](/chirp/) for regression testing

## Next steps

- Read [[docs/concepts/catalog-graph|Catalog graph]] for the mental model
- Customize branding in `app/docs.yaml` under `site:`
- Run `fura check` before you commit content changes
