---
title: Platform proof
description: The Furatena replacement thesis in one workflow.
weight: 55
lang: en
type: doc
tags: [demo, agent-exports, static-export, search]
category: concepts
---

# Platform proof

The replacement thesis is not "another markdown theme." Furatena turns one corpus into
four useful surfaces without adding a JavaScript app build.

## One corpus

Authors edit markdown, MDX, HTML, or RST through configured mounts:

```yaml
mounts:
  - id: docs
    content_root: content
    default: true
    extensions: [".md", ".mdx", ".html"]
```

The scanner adapts each source into `DocNode` records with Content IR: headings, links,
directives, plain text, sections, backlinks, and mount metadata.

## Four surfaces

| Surface | Command or URL | Why it matters |
|---------|----------------|----------------|
| Live authoring | `fura serve --author` | Edit content and refresh only affected page regions |
| Static hosting | `fura freeze` then `fura export` | Ship HTML to GitHub Pages or any static host |
| Human search | `/search` and `/search.json` | Query the same graph, not a separately generated Lunr build |
| Agent access | `/catalog.json`, `/tools.json`, `/llms-full.txt` | Give agents structure, retrieval, and source context |

## Validation loop

```bash
fura check --content-only --warnings-as-errors
fura query --heading "Platform proof"
fura serve
```

Those commands prove the important parts: the corpus parses, the graph is queryable, and
the same data can render a browser page.

## Why it beats a split stack

In a JS SSG, authoring, search, static output, and agent exports tend to become separate
build products. In Furatena, they are projections of the same catalog graph:

1. Source adapters produce normalized content.
2. The catalog graph resolves navigation, links, references, and chunks.
3. Views render HTML fragments for live and static delivery.
4. Export endpoints serialize the same graph for tools and agents.

That is the "best of both worlds": live hypermedia while authoring, static files when you
deploy, and machine-readable structure without a second indexing system.
