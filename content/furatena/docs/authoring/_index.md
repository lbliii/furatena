---
title: Authoring
description: Write markdown, directives, and navigation for Furatena corpora
draft: false
weight: 30
lang: en
type: doc
category: authoring
icon: pencil

cascade:
  type: doc
---

Author documentation in Patitas markdown with YAML front matter. Furatena indexes structure
at parse time — headings, links, and directives become Content IR on each `DocNode`.

## Topics

| Page | Covers |
|------|--------|
| [[docs/authoring/markdown|Markdown and front matter]] | Titles, layout, cascade, metadata |
| [[docs/authoring/directives|Directives]] | Cards, tabs, admonitions, code blocks |
| [[docs/authoring/navigation|Navigation]] | Weight, sections, sidebar scoping |
| [[docs/authoring/collections|Collections]] | Multi-node pillar pages |
| [[docs/authoring/lifecycle-workflow|Review and publish an author change]] | Studio, validation, lifecycle, public-output inspection |

## Quick example

```markdown
---
title: Installation
description: Install Furatena and run your first site
layout: doc
weight: 10
tags: [getting-started]
---

Body markdown with [[docs/concepts/catalog-graph|wikilinks]] and directives below.

:::{note}
Front matter `layout: doc` selects the standard documentation view.
:::
```

Try richer directives on the [home page](/) — the card grid is plain Patitas markdown.
