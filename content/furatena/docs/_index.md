---
title: Documentation
description: Guides and reference for building hypermedia documentation with Furatena
draft: false
weight: 10
lang: en
type: doc
keywords: [furatena, documentation, hypermedia, catalog, fura]
category: overview

cascade:
  type: doc
---

## Get oriented

Furatena turns markdown under `content/` into a live document graph served through an htmx
shell. You author pages, Furatena indexes structure at parse time, and views decide how
each node renders inside `#page-root`.

New here? Start with **Get Started**. Building a mental model? Read **Concepts**. Running
a site in production? Jump to **Operations** or the **Reference**.

## How this site is organized

| Lane | Use when |
|------|----------|
| [Get Started](/docs/get-started/) | First hour — install, quickstart, project layout |
| [Concepts](/docs/concepts/) | Catalog graph, dual IR, views, federation |
| [Authoring](/docs/authoring/) | Markdown, directives, navigation, collections |
| [Theming](/docs/theming/) | Tokens, skin, views, branding |
| [Operations](/docs/operations/) | Serve, freeze, export, check, deploy |
| [Reference](/docs/reference/) | CLI, config files, glossary |
| [About](/docs/about/) | Philosophy, Chirp relationship, roadmap |

Machine-readable doc index: [`/llms.txt`](/llms.txt) (generated on site build).

:::{cards}
:columns: 2
:gap: medium

:::{card} Get Started
:icon: rocket
:link: /docs/get-started/
Install Furatena, run `fura serve`, and edit your first page.
:::{/card}

:::{card} Concepts
:icon: layers
:link: /docs/concepts/
How the catalog graph, views, and shell fit together.
:::{/card}

:::{card} Authoring
:icon: pencil
:link: /docs/authoring/
Markdown, directives, front matter, and collections.
:::{/card}

:::{card} Operations
:icon: check-circle
:link: /docs/operations/
Serve modes, freeze, export, and CI checks.
:::{/card}

:::{/cards}
