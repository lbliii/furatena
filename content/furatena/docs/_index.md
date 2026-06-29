---
title: Documentation
description: Guides and reference for publishing Markdown docs with Furatena
draft: false
weight: 10
lang: en
type: doc
keywords: [furatena, documentation, markdown, static docs, fura]
category: overview

cascade:
  type: doc
---

## Get oriented

Furatena turns Markdown files into a searchable documentation site. Write locally,
preview changes as you go, and publish the same content as static pages when it is
ready.

New here? Start with **Get Started**. Building a mental model? Read **Concepts**. Running
a site in production? Jump to **Operations** or the **Reference**.

## How this site is organized

| Lane | Use when |
|------|----------|
| [Get Started](/docs/get-started/) | First hour: install, quickstart, project layout |
| [Concepts](/docs/concepts/) | How pages, navigation, search, and outputs fit together |
| [Authoring](/docs/authoring/) | Markdown, directives, navigation, collections |
| [Theming](/docs/theming/) | Tokens, skin, views, branding |
| [Operations](/docs/operations/) | Preview locally, build static pages, check links, deploy |
| [Reference](/docs/reference/) | CLI, config files, glossary |
| [About](/docs/about/) | Product principles, project direction, roadmap |

AI-ready page index: [`/llms.txt`](/llms.txt) (generated on site build).

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
How pages, navigation, search, and outputs fit together.
:::{/card}

:::{card} Authoring
:icon: pencil
:link: /docs/authoring/
Markdown, directives, front matter, and collections.
:::{/card}

:::{card} Operations
:icon: check-circle
:link: /docs/operations/
Local preview, static builds, deploys, and CI checks.
:::{/card}

:::{/cards}
