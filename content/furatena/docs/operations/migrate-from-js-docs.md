---
title: Migrate from JS docs
description: Move MDX-heavy docs from Mintlify, Fern, Docusaurus, or custom React stacks into Furatena.
weight: 35
lang: en
type: doc
tags: [migration, mdx, mintlify, fern, docusaurus]
category: operations
---

# Migrate from JS docs

Furatena's migration wedge is simple: keep the docs corpus, remove the build-time React
surface, and lower MDX components into catalog-native directives where possible.

## First pass

Run a dry migration against a standalone app or the current repository:

```bash
fura migrate --dry-run
```

Then migrate once the report looks right:

```bash
fura migrate
```

By default, `.mdx` files become `.md` siblings and the source `.mdx` file is removed.
Use `--keep-mdx` while evaluating:

```bash
fura migrate --keep-mdx
```

## What gets converted

| MDX shape | Furatena output |
|-----------|-----------------|
| Capitalized JSX block | Lowercase Patitas directive block |
| JSX attributes | Directive options |
| Self-closing JSX component | Empty directive block |
| Markdown content | Preserved as markdown |

Unmapped JSX components are reported as warnings so you can choose whether to add a
directive handler, rewrite the content, or keep the page as HTML.

## Compatibility diagnostics

`fura check` reports format compatibility gaps before migration:

| Source construct | Behavior |
|------------------|----------|
| MDX JSX component matching a Furatena directive | Recorded as mapped compatibility metadata |
| MDX JSX component without a directive mapping | Warning with source line and migration action |
| RST admonition directive | Recorded as mapped Content IR directive metadata |
| RST directive without a Furatena directive contract | Warning with source line and adapter behavior |
| RST role such as `:py:class:` | Warning that the target is not resolved through Furatena inventories |

Use these warnings as the first triage list for custom directive mappings or manual
cleanup. They are intentionally conservative: rendered HTML can still work while the
diagnostic tells you which constructs are not yet native Furatena contracts.

## Platform notes

| Source | Migration path |
|--------|----------------|
| Mintlify | Import the docs tree, convert MDX components, move navigation into `mounts.yaml` and front matter weights |
| Fern | Keep API/reference content as markdown or generated autodoc, then expose `/catalog.json` and `/tools.json` for agents |
| Docusaurus | Migrate docs/blog MDX first, then replace sidebars with Furatena catalog sections |

## Validate after migration

```bash
fura check --content-only --warnings-as-errors
fura query --directive cards
fura serve --author
```

The goal is not one-shot magic. The goal is to make the first conversion mechanical,
surface the small set of JSX components that still need decisions, and immediately get a
live docs app with static and agent exports from the same corpus.
