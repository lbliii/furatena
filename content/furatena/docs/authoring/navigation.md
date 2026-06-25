---
title: Navigation
description: Weight, sections, doc_list inference, and sidebar scoping
draft: false
weight: 30
lang: en
type: doc
tags: [navigation, sidebar, weight]
category: authoring
---

Furatena builds navigation from the **catalog graph** — folder structure, front matter
`weight`, and section roots. No separate nav config file is required for standard docs trees.

## Section hierarchy

Folders under `docs/` become nested sections:

```
docs/
├── get-started/     → section "get-started"
├── concepts/        → section "concepts"
└── reference/       → section "reference"
```

Each `_index.md` is the section landing page. Sibling pages sort by **`weight`** (ascending),
then title.

## Section indexes (`doc_list`)

When a page is a **section root with children**, Furatena infers the `doc_list` view even
if front matter says `layout: doc`:

- Renders a card/list index of child pages
- Uses `views/doc_list.html` unless overridden

Set explicitly when you want control:

```yaml
---
title: Concepts
layout: doc_list
weight: 20
---
```

## Sidebar scoping

On catalog-surface pages (`layout: doc`, `doc_list`, `collection`), the left rail shows
**only the current docs section** — not the entire site tree.

Example: on `/docs/concepts/catalog-graph/`, the rail lists Concepts pages, not Get Started
or Reference siblings at the top level.

## Prev / next

Linear **`nav_next`** and **`nav_prev`** edges connect pages within a section by weight.
Doc pages show sequential reading links in the footer when neighbors exist.

## App-surface navigation

Home, search, and portal pages use the **site top bar** configured in `app/docs.yaml`
under `site.navigation`. Catalog doc pages hide the top bar and use rail-only chrome.

Customize product name, mark, and mega-menu links via `site:` — see
[[docs/get-started/project-layout|Project layout]].

## Portal and federated mounts

With multiple mounts, `/portal/` lists corpora. Prefixed mounts (like `/chirp/`) keep their
own section sidebars scoped to that mount's docs tree.

See [[docs/concepts/federation|Federation]].

## Next

→ [[docs/authoring/collections|Collections]] — stitch multiple nodes into one reading surface.
