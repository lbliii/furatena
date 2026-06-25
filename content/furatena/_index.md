---
title: Furatena
description: Hypermedia documentation catalog — markdown indexed live, served as htmx fragments
layout: home
weight: 100
type: page
draft: false
lang: en
keywords: [furatena, documentation, hypermedia, htmx, catalog, markdown, fura]
category: home
---

## Markdown in. Hypermedia out.

**Live graph. htmx shell. Dual IR.**

Furatena indexes markdown corpora into an in-memory document graph and serves pages as
htmx fragments inside a persistent shell. Author in `content/`, run `fura serve`, and
ship the same catalog frozen or exported for GitHub Pages.

```bash
uv sync --group dev
uv run fura serve
```

Open http://127.0.0.1:8001/ — edit markdown and watch the open page reload via partial swaps.

---

## Logo sketches (draft)

Bold pre-Columbian gold shapes — flat tunjo silhouettes, no wire detail.

<div style="display:flex;gap:2rem;flex-wrap:wrap;align-items:flex-end;margin:1rem 0 0.5rem">
  <figure style="margin:0;text-align:center">
    <img src="/docs-theme/branding/mark.svg" alt="Twin tunjo mark" width="120" height="120">
    <figcaption style="font-size:0.85rem;margin-top:0.35rem">mark.svg — twin tunjos + emerald</figcaption>
  </figure>
  <figure style="margin:0;text-align:center">
    <img src="/docs-theme/branding/mark-mono.svg" alt="Mono tunjo mark" width="120" height="120">
    <figcaption style="font-size:0.85rem;margin-top:0.35rem">mark-mono.svg</figcaption>
  </figure>
  <figure style="margin:0;text-align:center">
    <img src="/docs-theme/branding/favicon.svg" alt="Favicon sketch" width="64" height="64">
    <figcaption style="font-size:0.85rem;margin-top:0.35rem">favicon.svg — single tunjo</figcaption>
  </figure>
</div>

Unicode marks for comparison: **𒀭** (one) · **𒀮** (two) · **𒀯** (three)

---

## Why Furatena

:::{cards}
:columns: 2
:gap: medium

:::{card} Live catalog graph
:icon: network
Every page is a node with typed edges — navigation, backlinks, search chunks, and agent tools read the same graph.
:::{/card}

:::{card} htmx-native shell
:icon: layers
Boosted navigation swaps `#page-root` while the shell, search modal, and theme persist across routes.
:::{/card}

:::{card} Dual IR
:icon: code
Patitas Content IR at index time plus Kida Presentation IR for views — lint, incremental reload, and agent queries without regex on HTML.
:::{/card}

:::{card} Freeze or export
:icon: download
`fura freeze` writes catalog metadata and HTML shards; `fura export` produces a static site for GitHub Pages.
:::{/card}

:::{card} Federated mounts
:icon: globe
Serve multiple corpora from one app — product docs, shared glossary, legacy content — with cross-mount links and a portal hub.
:::{/card}

:::{card} Built on Chirp
:icon: rocket
Same hypermedia stack as [Chirp](/chirp/) — server-rendered HTML, optional htmx at the edges, no npm required.
:::{/card}

:::{/cards}

## Common workflows

- Dogfood your own product docs from `content/<product>/`
- Keep a reference mount (like [Chirp docs](/chirp/)) for regression testing
- Run `fura check` in CI for broken links, directive contracts, and theme lint
- Expose `/catalog.json`, `/search.json`, and `/tools.json` for agents and IDE tooling

---

## The Bengal stack

Furatena is the documentation surface for the Bengal hypermedia ecosystem.

| | | |
|--:|---|---|
| **𒀭** | **Furatena** | Documentation catalog ← You are here |
| **ᗢ** | [Chirp](/chirp/) | Web framework |
| **=^..^=** | [Pounce](https://github.com/lbliii/pounce) | ASGI server |
| **)彡** | [Kida](https://github.com/lbliii/kida) | Template engine |
| **ฅᨐฅ** | [Patitas](https://github.com/lbliii/patitas) | Markdown parser |

Python-native. Free-threading ready. Markdown-first authoring.
