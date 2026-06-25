---
title: Glossary
description: Furatena, Fura, DCP, and catalog vocabulary
draft: false
weight: 40
lang: en
type: doc
tags: [glossary]
category: reference
---

:::{glossary}
:tags: furatena, catalog, hypermedia, dcp
:collapsed: false
:::

## Naming

| Name | Meaning |
|------|---------|
| **Furatena** | The hypermedia documentation catalog product (from the Muzo legend of Fura and Tena) |
| **Fura** | CLI shorthand (`fura serve`, `fura check`, …) |
| **Itoco** | Optional internal name for the catalog graph |

## Architecture terms

| Term | Definition |
|------|------------|
| **DocNode** | One indexed page in the catalog graph |
| **Content IR** | Patitas structure at index time — headings, links, directives |
| **Presentation IR** | Kida template composition — blocks, regions, context |
| **DCP** | Document Catalog Protocol — JSON schema for `/catalog.json` v3 |
| **Mount** | A federated content corpus with its own `content_root` and optional URL prefix |
| **Edition** | Version channel slice of the graph (e.g. `latest`, `0.8.0`) |
| **View kind** | Front matter label (`layout:`) selecting a page template |
| **Shell** | Persistent htmx frame (`#main`, search modal, theme menu) |
| **Boost** | htmx enhanced navigation swapping `#page-root` |

## Related reading

- [[docs/concepts/catalog-graph|Catalog graph]]
- [[docs/concepts/dual-ir|Dual IR]]
- [DCP specification](https://github.com/lbliii/furatena/blob/main/docs/DCP.md)

Terms are also defined in `data/glossary.yaml` for directive embeds across mounts.
