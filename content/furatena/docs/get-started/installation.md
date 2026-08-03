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

The CLI is **`fura`** (short for Furatena). `uv sync` installs it into the project
environment; it does not make bare `fura` globally available. From a checkout, use
`uv run fura …` as the canonical zero-install form.

## Verify the install

```bash
uv run fura --help
uv run fura theme list
```

You should see **docs-core** id `furatena` and skin pack `lagoon`.

## Optional bare command

Install an editable uv tool when you explicitly want to type `fura` without `uv run`:

```bash
uv tool install --editable .
fura --help
fura serve
```

The tool remains linked to this checkout. If `fura` is not found after installation,
run `uv tool update-shell`, restart the shell, and retry. `uv tool dir --bin` prints
the executable directory for manual `PATH` inspection.

## Optional: local Chirp co-development

Furatena depends on released **`bengal-chirp`** from PyPI. To develop against a sibling
Chirp checkout, uncomment `[tool.uv.sources]` in `pyproject.toml`.

## Next

→ [[docs/get-started/quickstart|Quickstart]] — run the default app and open the site.
