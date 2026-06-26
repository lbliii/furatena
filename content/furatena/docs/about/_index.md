---
title: About
description: Philosophy, ecosystem, and roadmap
draft: false
weight: 60
lang: en
type: doc
category: about
icon: info

cascade:
  type: doc
---

## Pages

| Page | Covers |
|------|--------|
| [[docs/about/philosophy|Philosophy]] | Why hypermedia docs, not a SPA |
| [[docs/about/chirp-relationship|Chirp relationship]] | Runtime stack and Chirp docs deploy |
| [[docs/about/roadmap|Roadmap]] | Delivery waves and what's next |

## What Furatena is

Furatena turns markdown into a live documentation site — searchable,
navigable, and ready to export.

**For authors:** edit a file and the page you're viewing updates instantly.
No rebuild loop. No npm toolchain for writing.

**For teams:** one indexed catalog powers nav, search, link checking, and
agent exports (`/catalog.json`, `/search.json`, `/tools.json`).

**Under the hood:** markdown becomes a queryable graph; pages swap as
hypermedia fragments inside a persistent shell. Static export for
GitHub Pages uses the same corpus.

The CLI is **Fura**.
