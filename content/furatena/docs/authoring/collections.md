---
title: Collections
description: Multi-node pillar pages with layout collection
draft: false
weight: 40
lang: en
type: doc
tags: [collections, compose, read-through]
category: authoring
---

A **collection view** stitches ordered catalog nodes into one continuous reading surface —
no duplicated markdown, no static export step. The graph defines membership; the view
renders member bodies inline.

## Define a collection

**1. Front matter** on the pillar page:

```yaml
---
title: Get Started — Read-through
description: One continuous reading surface for the Get Started lane
layout: collection
collection: get-started
weight: 2
---
```

**2. Node list** in `data/collections.yaml`:

```yaml
get-started:
  title: Get Started
  description: Install Furatena and run your first documentation site.
  nodes:
    - docs/get-started
    - docs/get-started/installation
    - docs/get-started/quickstart
    - docs/get-started/project-layout
```

**3. Compose hook** in `app/docs.yaml`:

```yaml
compose:
  collection:
    data: ../data/collections.yaml
```

## What renders

`views/collection.html`:

- Table of contents across all member headings ("In this collection")
- Member bodies in YAML order with stable anchors
- Collection chrome (`chirp-theme-track-layout`)

## When to use collections

| Use collection | Use separate pages + nav |
|----------------|--------------------------|
| Linear onboarding read-through | Reference material browsed out of order |
| Pillar landing that must inline member content | Deep sections with many siblings |
| Agent/print-friendly single URL | Pages that change independently |

## Validation

`fura check` warns when front matter references an unknown `collection:` id or when
`collections.yaml` slugs do not resolve in the graph.

## Next

→ [[docs/operations/serve-and-author|Serve and author]] — run the live catalog while editing.
