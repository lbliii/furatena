---
title: Dual IR
description: Content IR from Patitas and Presentation IR from Kida views
draft: false
weight: 20
lang: en
type: doc
tags: [content-ir, patitas, kida]
category: concepts
---

Furatena aligns two intermediate representations:

```mermaid
flowchart LR
  MD[Markdown]
  P[Patitas]
  CIR[Content IR]
  BH[body_html]
  KV[Kida views]
  PIR[Presentation IR]
  PR["#page-root HTML"]

  MD --> P --> CIR --> BH
  KV --> PIR --> PR
  CIR -.->|"TOC, search, lint"| PR
```

**Content IR** captures headings, links, and directives at index time. **Presentation IR**
captures view template blocks and context contracts.

The catalog graph connects them — TOC, backlinks, lint, incremental reload, and agent
queries read structure instead of scraping HTML.

See the design doc [Dual IR](https://github.com/lbliii/furatena/blob/main/docs/DUAL_IR.md) in the repository.
