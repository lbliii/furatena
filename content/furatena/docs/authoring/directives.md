---
title: Directives
description: Cards, tabs, admonitions, and other Patitas rich blocks
draft: false
weight: 20
lang: en
type: doc
tags: [directives, patitas, cards, tabs]
category: authoring
---

Directives are **rich blocks inside markdown**. Furatena registers handlers that render
to chirp-ui HTML and record directive names in Content IR for lint and agent queries.

## Admonitions

```markdown
:::{note}
Front matter `layout: doc` selects the standard documentation view.
:::

:::{tip}
Run `fura check --content-only` while authoring — catches broken links early.
:::

:::{warning}
Unknown directive names produce `fura check` warnings.
:::
```

Variants include `note`, `tip`, `important`, `warning`, `caution`, and `danger`.

## Cards

Grid of linked or static cards — used heavily on the [home page](/):

```markdown
:::{cards}
:columns: 2
:gap: medium

:::{card} Get Started
:icon: rocket
:link: /docs/get-started/
Install Furatena and run your first docs site.
:::{/card}

:::{card} Concepts
:icon: layers
:link: /docs/concepts/
Catalog graph, views, and dual IR.
:::{/card}

:::{/cards}
```

## Tabs

```markdown
::::{tabs}
:::{tab} uv
```bash
uv run fura serve
```
:::{/tab}
:::{tab} make
```bash
make serve
```
:::{/tab}
::::{/tabs}
```

Synced code tabs use `::::{code-tabs}` with `:sync:` keys (see Chirp install pages under `/chirp/docs/get-started/installation/`).

## Steps

Multi-step procedures with stable anchors:

```markdown
:::{steps}

:::{step} Install dependencies
Run `uv sync --group dev` from the repository root.
:::{/step}

:::{step} Start the server
Run `uv run fura serve` and open http://127.0.0.1:8001/
:::{/step}

:::{/steps}
```

## Glossary embed

Pull terms from `data/glossary.yaml`:

```markdown
:::{glossary}
:tags: furatena, catalog
:collapsed: true
:::
```

## Related and child cards

```markdown
:::{related}
- /docs/concepts/catalog-graph/
- /docs/operations/check-and-lint/
:::

:::{child-cards}
:columns: 2
:::
```

`child-cards` auto-lists direct child pages of the current section.

## Validation

`fura check` validates directive nesting contracts (tabs inside tabs, step ordering, etc.)
via `catalog/content_lint.py`. Unknown directive names produce warnings.

Query indexed directives across the corpus:

```bash
fura query --directive cards
fura query --directive tabs --mount furatena
```

## Next

→ [[docs/authoring/navigation|Navigation]] — weight, sections, and sidebar scoping.
