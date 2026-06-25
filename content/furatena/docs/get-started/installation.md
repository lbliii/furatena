---
title: Installation
description: Install Furatena and development dependencies
draft: false
weight: 10
lang: en
type: doc
tags: [installation, setup]
keywords: [install, uv, pip, fura]
category: onboarding
---

Install Furatena from this repository (or your fork) and sync development dependencies.

## Prerequisites

- **Python 3.14+**

## Clone and sync

```bash
git clone https://github.com/lbliii/furatena.git
cd furatena
uv sync --group dev
```

The CLI is **`fura`** (short for Furatena). Run it via `uv run fura …` or activate `.venv/bin/`.

## Verify the install

```bash
uv run fura --help
uv run fura theme list
```

You should see **docs-core** id `furatena` and skin pack `lagoon`.

## Optional: local Chirp co-development

Furatena depends on released **`bengal-chirp`** from PyPI. To develop against a sibling
Chirp checkout, uncomment `[tool.uv.sources]` in `pyproject.toml`.

## Next

→ [[docs/get-started/quickstart|Quickstart]] — run the default app and open the site.
