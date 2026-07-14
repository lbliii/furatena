---
title: One control plane for technical knowledge
description: Connect technical sources to one governed graph, then project consistent documentation and agent context from it.
layout: marketing_page
type: page
weight: 20
draft: false
lang: en
keywords: [documentation control plane, technical knowledge graph, documentation governance, platform documentation]
category: product
---

Technical documentation has escaped the website. The same knowledge now feeds product
guides, API references, search, release workflows, coding agents, support systems, and
internal tools. When each surface maintains its own index and policy, they inevitably
disagree.

Furatena puts a governed content graph between technical sources and delivery surfaces.
Teams connect the corpus once, apply structure and policy once, and publish many
independently useful projections from the result.

## The control-plane model

| Layer | What enters or happens here | What it prevents |
|---|---|---|
| **Sources** | Markdown, MDX, MyST, RST, HTML, OpenAPI, Python objects, and mounted repositories | A forced all-at-once rewrite into one authoring syntax |
| **Content graph** | Pages, sections, API operations, objects, releases, owners, relationships, chunks, and source provenance | Separate search, reference, and agent corpora drifting apart |
| **Governance** | Validation, access, editions, lifecycle, freshness, ownership, compatibility, and publication evidence | Policy being recreated as prompts or frontend conventions |
| **Surfaces** | Web pages, static releases, Markdown sidecars, search, inventories, manifests, retrieval, and MCP | Human and agent documentation telling different stories |

## Govern the corpus, not only the renderer

A page is more than HTML. Furatena records its source, normalized structure,
relationships, edition, visibility, lifecycle, and retrieval chunks. Those decisions are
available to the browser, build pipeline, search layer, and agent interfaces instead of
being trapped inside a theme.

:::{cards}
:columns: 3
:gap: medium

:::{card} Typed relationships
:icon: layers
Connect guides, references, API operations, releases, implementations, translations,
owners, and superseding editions through explicit graph edges.
:::{/card}

:::{card} Shared policy boundary
:icon: check-circle
Apply access and lifecycle decisions before content reaches public search, static
artifacts, catalog exports, or agent retrieval.
:::{/card}

:::{card} Inspectable contracts
:icon: code
Publish versioned catalogs, routes, tools, channels, structures, inventories, and
retrieval fixtures that platform teams can test in CI.
:::{/card}

:::{/cards}

## One source decision, several delivery modes

Furatena does not require every audience to use the same runtime. A public documentation
site can remain a durable static artifact while a trusted application provides dynamic
query or retrieval. Both derive from the same graph and declare their capability
differences explicitly.

| Delivery mode | Best for | Control-plane output |
|---|---|---|
| Static | Public docs, CDNs, immutable releases, offline use | HTML, Markdown and text sidecars, search, graph, inventories, manifests |
| Application | Dynamic query, semantic retrieval, gated experiences, integrations | Negotiated pages, search and retrieval endpoints, operational status |
| Local and CI | Authoring, validation, migration, evaluation, release gates | Diagnostics, impact reports, contract checks, reproducible artifacts |
| Agent process | Tool use and source-grounded retrieval | MCP resources and tools governed by the same catalog policy |

## Designed for heterogeneous documentation estates

Platform teams rarely begin with a clean Markdown folder. They inherit several
repositories, generated references, product versions, old Sphinx projects, MDX
components, and private operational knowledge. Furatena uses mounts, adapters, and
reference inventories to federate those sources before requiring consolidation.

That makes migration an analysis problem first and a conversion problem second. See
[how Furatena assesses an existing corpus](/migration/).

## Human and agent parity

An agent should not receive a convenient approximation of the documentation. It should
receive the correct edition, authorized scope, source context, and freshness state that
a human workflow would rely on. Because both surfaces originate in the same graph,
teams can test those invariants instead of hoping a downstream crawler preserved them.

Review changes through [governed pull-request previews](/platform/governed-pr-previews/)
before they reach either surface. One protected, commit-bound environment exposes the
human site and agent outputs with shared conformance evidence.

[Explore governed agent delivery](/agents/) or [inspect the platform proof](/proof/).
