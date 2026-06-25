---
title: Hypermedia model
description: htmx boost, fragments, and author reload
draft: false
weight: 40
lang: en
type: doc
category: concepts
---

Furatena serves documentation as hypermedia:

- **Boosted links** — internal navigation swaps `#page-root` via htmx
- **Author reload** — markdown edits trigger partial OOB swaps (`toc-panel`, `head-meta`, `page-root`)
- **Shell persistence** — search, theme, and nav chrome survive route changes

`fura check` validates that internal links receive boost attributes via `boost_doc_links`.
