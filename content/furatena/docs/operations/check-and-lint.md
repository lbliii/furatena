---
title: Check and lint
owner: platform-docs
reviewed_at: "2026-07-07"
description: fura check, content lint, theme lint, and CI gates
draft: false
weight: 30
lang: en
type: doc
tags: [check, lint, ci, validation]
category: operations
---

`fura check` is the unified validation gate — hypermedia contracts (via Chirp `app.check()`),
corpus lint, theme lint, and DCP schema validation.

## Basic usage

```bash
uv run fura check                  # full check
uv run fura check --content-only   # skip hypermedia — content + views + theme
```

Exit code is non-zero when errors are found — suitable for CI.

## Content lint

Runs on every indexed page via Content IR:

| Rule | Example failure |
|------|-----------------|
| Broken internal links | `[missing](/docs/no-such-page/)` |
| Heading increment | `###` immediately after `#` |
| Empty links / headings | `[](url)` or `##` with no text |
| Directive contracts | Unclosed tabs, invalid step nesting |
| Unknown directives | `:::{unknown}` |
| Front matter | Unknown `collection:` id, invalid view kind |
| Cross-edition links | Link to a page on a different release channel |

Federated mounts: links like `/docs/foo` from a prefixed mount (e.g. shared at `/shared/`)
resolve against the correct mount prefix during check.

Strict edition mismatches:

```bash
fura check --strict-edition-links
```

## Hypermedia check

When not using `--content-only`, Furatena runs Chirp **`app.check()`**:

- Route references from templates (`hx-get`, `action`, …)
- Fragment target contracts
- Boosted-link audit — internal links must get `hx-boost` via `boost_doc_links`

## Theme lint

Validates `docs.yaml` theme config:

- Unknown `theme.id` or missing docs-core pack
- Missing vendor assets, favicon
- Kida view template context contracts

List installed theme packs:

```bash
fura theme list
```

## DCP validation

Live **`/catalog.json`** export is validated against **`catalog-v3.schema.json`**
shipped in `src/furatena/catalog/schemas/`.

## Retrieval regression gate

`fura evals` compares the versioned known-answer dataset with its packaged
Recall@3, MRR, no-result, stale-answer, and private-leak thresholds. An
unapproved regression emits `fura.evals.retrieval_regression` and exits with a
validation error. Restore the metric or pass
`--approve-retrieval-regression "reason"` for an intentional, auditable
transition.

## Query and structure

Inspect indexed structure without opening the browser:

```bash
fura query --directive tabs
fura query --mount furatena --tag concepts
```

At freeze, **`structure.json`** captures a flat directive + heading index with source lines.

## Migration readiness

Before switching a corpus over, run:

```bash
fura migrate --report --json
```

The migration report reuses `fura check` diagnostics and adds format compatibility
findings for MDX, RST, and MyST. JSON output groups findings by severity, source
path, construct, and suggested action so teams can triage unsupported constructs
and risky links before applying migrations.

For a browser view while authoring locally, run `fura serve --author` and open
`/docs/_author/dashboard`. The dashboard lists mounts, source formats, page counts,
freshness, and lint blockers without requiring cloud services.

## Recommended CI job

```bash
uv sync --group dev
uv run fura check --content-only   # fast corpus gate
uv run fura check                  # full gate before release
```

## Next

→ [[docs/operations/deploy|Deploy]] — environment variables and production URLs.
