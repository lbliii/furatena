---
title: Understand migration risk before rewriting the corpus
description: Inventory mixed documentation sources, separate safe conversions from manual blockers, and preserve the knowledge needed by every output surface.
layout: marketing_page
type: page
weight: 40
draft: false
lang: en
keywords: [documentation migration, MDX migration, Sphinx migration, MyST migration, OpenAPI documentation]
category: product
---

Documentation migrations fail in the details: custom components, broken references,
redirects, version assumptions, generated pages, private content, and navigation rules
that were never written down.

Furatena treats migration as a structured assessment before it becomes a source rewrite.
The goal is to show what can move safely, what needs a deliberate mapping, and what each
decision will affect downstream.

## A migration workflow for platform teams

1. **Inventory the estate.** Discover repositories, mounts, formats, pages, generated
   references, owners, versions, links, and publication surfaces.
2. **Normalize without erasing provenance.** Adapt supported sources into the content
   graph while retaining their origin and format-specific diagnostics.
3. **Separate safe work from manual work.** Report reversible conversions, unsupported
   component families, unresolved references, access risks, and structural warnings.
4. **Validate the target surfaces.** Check the browser, static artifact, search index,
   reference graph, and agent outputs before cutover.

## Bring the sources you already have

| Source | Migration role |
|---|---|
| Markdown | First-class authoring with front matter, directives, links, and local preview |
| MDX | JSX-aware lowering into typed directives, with unsupported components reported |
| MyST and RST | Technical structure, roles, directives, and reference-inventory integration |
| HTML | Bridge existing pages by extracting headings, links, text, and structured markers |
| OpenAPI | Project operations, schemas, examples, authentication, and source metadata into reference nodes |
| Python code | Generate object documentation that participates in the same graph and inventories |

Custom adapters and multiple mounts let teams federate content before deciding which
sources should be converted permanently.

## Evidence from the migration pilots

The recorded Furatena pilots analyzed **981 source files** and found **91.5% clean on the
first pass**. That top-line number is useful only with the blockers visible:

- The MDX corpus contained 953 sources and 80 blocking files.
- 302 of 931 applicable MDX files were classified as safe reversible conversions.
- 629 required manual treatment, often around product-specific components such as
  response fields, cards, frames, steps, notes, badges, and code groups.
- The Sphinx/MyST pilot surfaced extension and directive mappings that an importer could
  not safely infer.

The product promise is not one-click conversion. It is **knowing the real migration
shape before committing the team to it**.

## What a useful assessment should produce

:::{cards}
:columns: 2
:gap: medium

:::{card} Conversion report
:icon: check-circle
Safe changes, manual blockers, unsupported construct families, source locations, and
recommended mappings grouped into actionable work.
:::{/card}

:::{card} Information architecture findings
:icon: layers
Orphans, duplicate concepts, broken links, navigation depth, cross-edition links,
redirect needs, and ownership gaps.
:::{/card}

:::{card} Access and output audit
:icon: file-text
Which nodes are public, private, stale, or absent from static, search, reference, and
agent-facing artifacts.
:::{/card}

:::{card} Cutover evidence
:icon: rocket
Reproducible checks for the target routes, artifacts, references, retrieval answers,
and deployment profile.
:::{/card}

:::{/cards}

Start with the [migration operations guide](/docs/operations/migrate-from-js-docs/) or
[inspect the pilot evidence](/proof/).
