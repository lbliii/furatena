---
title: Governed documentation for agents
description: Give AI agents current, authorized, source-grounded technical context instead of an undifferentiated website scrape.
layout: marketing_page
type: page
weight: 30
draft: false
lang: en
keywords: [agent documentation, MCP documentation, AI retrieval, documentation governance, agent context]
category: product
---

Giving an agent Markdown is easy. Giving it the **correct, current, authorized context**
is the hard part.

Most agent documentation strategies begin after publication: crawl a site, split the
text, add embeddings, and hope the resulting index still reflects versions, access
boundaries, source relationships, and review state. Furatena applies those decisions in
the content control plane before retrieval.

## Context needs policy, not only a file format

| Control | Question it answers |
|---|---|
| Access | Is this actor allowed to retrieve the node or any derived chunk? |
| Edition | Which product or documentation version does this answer describe? |
| Freshness | Has the source changed since the public or agent artifact was built? |
| Provenance | Which repository, path, spec, or generated object produced the answer? |
| Relationships | What does this page explain, implement, supersede, validate, or depend on? |
| Scope | Which mount, tag, URL family, tenant, or knowledge domain belongs in the result? |

These controls are useful to humans, CI, search, and agents because they belong to the
corpus rather than a single chatbot implementation.

## Static artifacts and live retrieval

Furatena supports two complementary ways to serve agent-facing documentation.

:::{cards}
:columns: 2
:gap: medium

:::{card} Durable agent artifacts
:icon: file-text
Publish per-page Markdown, `llms.txt`, the full corpus, search and semantic indexes,
structure data, inventories, tool declarations, and the catalog graph beside the static
site. Agents can consume the release without depending on a hosted retrieval service.
:::{/card}

:::{card} Governed live retrieval
:icon: search
Run filtered search, node retrieval, backlinks, graph traversal, and source-health
operations against the application or a local MCP process when an interactive workflow
needs them.
:::{/card}

:::{/cards}

The distinction is explicit: a tool manifest describes available operations, while an
MCP transport is a separately operated interface. Publishing `tools.json` does not
pretend that a remote MCP server exists.

## One answer can be traced across surfaces

The same source node can produce:

1. A documentation page for a developer.
2. A Markdown or text representation for direct consumption.
3. Search and semantic chunks with the same node identity.
4. Backlinks and typed graph relationships.
5. An agent result that cites the node and its source metadata.

This makes human-agent consistency testable. Teams can compare an answer with the exact
page, edition, and release artifact that supports it.

## Guarded author operations

Agent workflows can go beyond reading, but mutation requires a different trust model.
Furatena's author-capable MCP path is opt-in, defaults mutations to dry-run behavior,
requires explicit confirmation, and can check the expected source revision. Read-only
public artifacts remain separate from trusted author operations.

## Inspect the current contracts

- [Agent tool manifest](/tools.json)
- [Catalog graph](/catalog.json)
- [Semantic index](/semantic.json)
- [Structure index](/structure.json)
- [Full agent corpus](/llms-full.txt)
- [Agent integration guide](/docs/operations/consume-agent-outputs/)

[See the complete platform proof](/proof/).
