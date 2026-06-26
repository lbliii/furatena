---
title: Catalog graph
description: DocNode, edges, mounts, and the in-memory documentation graph
draft: false
weight: 10
lang: en
type: doc
tags: [catalog, graph, dcp]
category: concepts
---

Every indexed page is a **`DocNode`** in an in-memory graph. Furatena builds this graph at
serve or freeze time from markdown under `content/`.

## Nodes

Each markdown file becomes a node with:

- **slug** — stable id (`docs/get-started/quickstart`)
- **url** — public path (`/docs/get-started/quickstart/`)
- **mount** — which corpus it belongs to (`furatena`, `chirp`, …)
- **Content IR** — headings, links, directives from Patitas parse
- **body_html** — rendered HTML for display and search

## Edges

The graph records typed relationships:

| Edge | Meaning |
|------|---------|
| `parent` | Section hierarchy from folder structure |
| `link` | Internal wikilinks and markdown links |
| `nav_next` / `nav_prev` | Linear reading order within a section |
| `tag` | Shared tags across pages |

Exports: [`/catalog.json`](/catalog.json) (DCP v3), [`/search.json`](/search.json), [`/tools.json`](/tools.json).

## Mounts

`app/mounts.yaml` federates multiple corpora into one registry. The **default** mount serves `/` without a prefix; others use `url_prefix` (for example `/shared/`).

## Editions and channels

Release notes under `releases/` and `doc_version` front matter enable version channels. Set `FURA_CHANNEL=latest` or a specific release id.

## Next

→ [[docs/concepts/dual-ir|Dual IR]] — structure at index time, not regex on HTML.
