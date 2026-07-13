---
title: Build with the Furatena content control plane
description: Guides and reference for connecting technical sources, governing the content graph, and delivering human and agent documentation.
draft: false
weight: 10
lang: en
type: doc
keywords: [furatena, content control plane, documentation graph, agent documentation, fura]
category: overview

cascade:
  type: doc
---

## Start with the control-plane model

Furatena connects technical sources to one governed content graph, then projects that
graph into browser documentation, static releases, search, references, and agent-facing
artifacts. The documentation is organized around the lifecycle of that corpus: adopt,
author, publish, operate, and integrate.

New to the product category? Read the [platform overview](/platform/) first. Evaluating
with an existing estate? Start with [migration](/migration/). Connecting an agent or
retrieval workflow? Begin with [agent delivery](/agents/).

Choose the journey that matches the job in front of you. The underlying guide URLs
stay stable, so existing links and bookmarks continue to work.

## How this site is organized

| Journey | Start here when you need to… |
|---------|-----------------------------|
| [Adopt](/docs/get-started/) | Evaluate Furatena, connect a corpus, and prove the first workflow |
| [Author](/docs/authoring/) | Write pages, shape navigation, and inspect the normalized content model |
| [Publish](/docs/operations/) | Freeze, export, validate, and deploy governed artifacts |
| [Operate](/docs/operations/serve-and-author/) | Run authoring, quality, observability, and recovery loops |
| [Integrate](/docs/operations/consume-agent-outputs/) | Connect agents, catalogs, references, APIs, and platform tooling |

AI-ready page index: [`/llms.txt`](/llms.txt) (generated on site build).

:::{cards}
:columns: 2
:gap: medium

:::{card} Adopt
:icon: book-open
:link: /docs/get-started/
Install Furatena, edit your first page, and choose a starter path.
:::{/card}

:::{card} Author
:icon: pencil
:link: /docs/authoring/
Write technical content, compose collections, and apply the existing theme system.
:::{/card}

:::{card} Publish
:icon: rocket
:link: /docs/operations/
Freeze, export, validate, and deploy browser and machine-readable artifacts.
:::{/card}

:::{card} Operate
:icon: check-circle
:link: /docs/operations/serve-and-author/
Run local authoring, CI quality gates, telemetry, and recovery.
:::{/card}

:::{card} Integrate
:icon: code
:link: /docs/operations/consume-agent-outputs/
Use agent artifacts, retrieval, MCP, catalog contracts, and platform reference.
:::{/card}

:::{/cards}
