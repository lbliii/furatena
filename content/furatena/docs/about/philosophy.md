---
title: Philosophy
description: Hypermedia docs as data — why Furatena exists
draft: false
weight: 10
lang: en
type: doc
tags: [philosophy, hypermedia]
category: about
---

Furatena treats documentation as a **live catalog graph**, not a build artifact you regenerate
on every edit.

## Docs as data

Traditional static site generators compile markdown → HTML once per build. Furatena indexes
markdown → **`DocNode`** at serve time (or freeze time) and keeps structure in Content IR:

- Headings, links, and directives are queryable without regex on HTML
- Backlinks, search chunks, and agent tools read the same graph
- Edits invalidate only affected regions — not the entire site

## Hypermedia, not a SPA

The browser receives **HTML fragments** over htmx boosted navigation. The shell (search,
theme, nav chrome) persists; `#page-root` swaps on each route.

No client framework. No npm build step for authoring. Python indexes content; Kida renders
views; Chirp serves hypermedia.

## Dual IR

**Content IR** (Patitas) captures author intent at index time.
**Presentation IR** (Kida) captures template composition.

Lint, incremental reload, TOC, and `/catalog.json` export use structure — not rendered string
scraping.

See [[docs/concepts/dual-ir|Dual IR]].

## Agent-native by design

Exports ship with the product:

- `/catalog.json` — DCP v3 graph
- `/search.json` and `/search/semantic` — keyword + hybrid retrieval
- `/tools.json` — tool manifest for agents
- `/llms.txt` — page index

Documentation sites should be as machine-readable as they are human-readable.

## Who Furatena is for

- Teams shipping product docs from markdown who want **live reload** while authoring
- Projects already on **Chirp** who need a documentation surface without a separate SSG
- Platforms that need **federated mounts** — product docs + shared glossary + legacy corpus

## Non-goals

Furatena is not a general-purpose CMS, not a wiki, and not a replacement for Chirp's
application framework — it is the **documentation catalog layer** on top of Chirp.

## Next

→ [[docs/about/chirp-relationship|Chirp relationship]]
