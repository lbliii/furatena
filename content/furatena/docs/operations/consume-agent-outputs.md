---
title: Consume agent outputs and MCP
owner: agent-platform
reviewed_at: "2026-07-07"
description: Choose and verify llms, page indexes, catalog, tools, channels, and MCP resources.
weight: 32
lang: en
type: doc
tags: [agents, mcp, llms, catalog, tools, channels]
category: operations
---

# Consume agent outputs and MCP

Furatena projects one catalog into static files, live HTTP routes, and MCP. Choose
the smallest surface that answers the consumer's question.

## 1. Build a stable snapshot

```bash
export PYTHON_GIL=0
uv run fura freeze
uv run fura export
```

Use frozen/static output for release automation and repeatable ingestion. Use live
routes while authoring when a consumer needs the newest indexed source.

## 2. Choose an output

| Need | Surface | Use it for |
|---|---|---|
| Site-level discovery | `/llms.txt` | Compact titles, descriptions, and page URLs |
| Offline full-text context | `/llms-full.txt` | Public corpus ingestion without HTML parsing |
| One page as authored text | `/docs/PATH/index.txt` | Focused retrieval, review, or diffing |
| Structured pages and graph | `/catalog.json` | Nodes, chunks, links, provenance, ownership, editions |
| Search records | `/search.json` | Keyword/local search integration |
| Tool planning | `/tools.json` | Agent-facing operations and input/output contracts |
| Release/output selection | `/channels.json` | Static, agent, PDF, and live channel status/artifacts |
| Interactive retrieval/actions | `fura mcp` | Resources, graph queries, safe author workflows |

All public outputs apply the same visibility/export policy. Draft, private,
internal, unlisted, and archived source does not become public agent context.

## 3. Inspect release metadata before ingesting

```bash
jq '.site, .channel, .source_fingerprint, .channels' app/public/channels.json
jq '.schema_version, .version, (.pages | length)' app/public/catalog.json
jq '.tools[].name' app/public/tools.json
```

Record the channel, catalog/schema version, source fingerprint, and artifact URL
with the ingestion job. Use `fura agent-diff OLD.json NEW.json --json` before
accepting a versioned contract change; breaking removals or URL/type changes need
an explicit compatibility decision.

## 4. Discover MCP without starting a client session

```bash
uv run fura mcp --preview --describe --json
```

The response lists stable resources such as `fura://catalog/graph`,
`fura://catalog/structure`, `fura://catalog/channels`, and
`fura://reports/validation`, plus tools such as `semantic_search`,
`retrieve_node`, and `query_graph`. API-enabled catalogs also expose
`list_api_operations`.

For a local MCP client, configure the command as `uv run fura mcp --preview` for a
frozen public snapshot or `uv run fura mcp --author` for current public source.
Only a trusted local author workflow should add `--include-private`. Remote author
sessions require explicit roles, actor/tenant/site metadata, rate/output bounds,
and a privileged token before sensitive tools or private context are exposed.

## 5. Verify known answers and boundaries

```bash
uv run fura evals --json
uv run fura check --agent --json
```

The deterministic evals cover prose retrieval, API operation discovery,
version/channel metadata, multi-mount queries, stale impact, and private-content
boundaries. The agent check validates MCP schemas/descriptions, Milo adapter parity,
sidecar alignment, mutation boundaries, and stale/private safety.

When ingestion returns a stale or missing answer, compare `channels.json` and the
source fingerprint first. Refresh freeze/export rather than silently mixing a new
source checkout with old agent files.
