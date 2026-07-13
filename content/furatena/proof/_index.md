---
title: Inspect the platform proof
description: Explore Furatena's live graph, artifacts, migration pilots, API-reference scale, retrieval evaluation, and declared deployment boundaries.
layout: marketing_page
type: page
weight: 50
draft: false
lang: en
keywords: [Furatena proof, documentation graph, agent artifacts, migration benchmark, documentation platform]
category: product
---

Furatena should be evaluated through inspectable artifacts and reproducible workflows,
not a checklist of AI features. This dogfood site is built by Furatena and publishes the
same contracts that power its human pages, search, references, and agent surfaces.

Research snapshot: **2026-07-13**.

## Follow one corpus into its outputs

| Output | Inspect it | What it proves |
|---|---|---|
| Human documentation | [Documentation](/docs/) | The graph renders a navigable documentation experience |
| Markdown representation | [Documentation index as Markdown](/docs.md) | Human pages retain a clean machine-readable representation |
| Catalog graph | [`catalog.json`](/catalog.json) | Nodes, metadata, relationships, and source identity are serialized |
| Structure and retrieval | [`structure.json`](/structure.json) and [`semantic.json`](/semantic.json) | Section structure and retrieval chunks derive from the same corpus |
| Agent discovery | [`tools.json`](/tools.json) and [`llms.txt`](/llms.txt) | Agent operations and corpus entry points ship with the release |
| Routes and channels | [`routes.json`](/routes.json) and [`channels.json`](/channels.json) | Browser and publication surfaces declare their contracts |
| Cross-reference inventory | [`objects.inv`](/objects.inv) | Technical references remain usable outside the rendered site |

The current committed public artifact contains 257 catalog pages, 2,063 graph edges, 58
declared application routes, and 1,060 static files. Counts are a scope indicator, not a
quality metric; the useful proof is that the outputs can be inspected and compared.

## Migration proof

The migration pilots cover 981 Markdown, MDX, MyST, and RST sources with a 91.5% clean
first pass. The reports retain the difficult part: 629 MDX files required manual
treatment rather than being labeled as safely reversible. See [the migration model and
full boundary](/migration/).

## API-reference proof

A fixed GitHub REST OpenAPI corpus exercised approximately 1,200 operations and paths,
including generated pages, examples, schemas, source metadata, static sidecars, and a
breaking-change comparison across versions. A separate Flask/Werkzeug/local pilot
exercised multi-mount references and verified that a private sentinel appeared in zero
public artifacts.

## Agent and retrieval proof

The repository includes deterministic known-answer fixtures, tool-selection checks,
contract-diff tooling, and filters for mount, edition, tag, URL, and access. The current
baseline is intentionally small—six positive retrieval cases—so it is engineering
telemetry rather than a broad quality claim.

The important safety invariant is shared filtering: public browser search, static
exports, catalogs, tools, and MCP resources derive visibility decisions from the same
access boundary.

## Deployment boundaries are part of the proof

| Surface | What it provides | Boundary |
|---|---|---|
| Static site | Complete browsable pages and bulk machine-readable artifacts | No runtime query or MCP transport |
| Deployed application | Content negotiation, query, search, retrieval, and health endpoints | Current proof uses a frozen catalog that changes through redeploy |
| Local MCP process | Catalog resources, retrieval tools, graph traversal, checks, and guarded author operations | Operated separately from the deployed web service |
| Local author mode | Dashboard, source inspection, validation, save/create, and lifecycle workflows | Remote use requires a trusted gateway identity integration |

These distinctions prevent discovery metadata from being mistaken for a running
service, or a dynamic preview from being described as continuous content synchronization.

## Reproduce the workflow

The technical [platform proof](/docs/concepts/platform-proof/) walks through source
adapters, graph construction, live authoring, static export, search, and agent artifacts.
The [static-versus-live readiness report](/docs/concepts/static-vs-live-agent-readiness/)
documents which capabilities are available through each channel.

[Explore the platform architecture](/platform/) or [read the implementation docs](/docs/).
