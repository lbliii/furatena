---
title: Static versus live agent readiness
owner: docs-product
reviewed_at: "2026-07-10"
description: Choose GitHub Pages, Railway, or both for agent-facing documentation
draft: false
weight: 58
lang: en
type: doc
tags: [agents, github-pages, railway, deployment, scorecard]
category: concepts
---

Furatena publishes the same documentation through two channels with different
strengths. GitHub Pages is an immutable export. Railway is an application that
can interpret a request. The evidence supports using both: Railway for
interactive agent clients and Pages as the durable read-only fallback.

## Measured result

On 2026-07-10, afdocs 0.18.7 checked six equivalent pages against
Agent-Friendly Documentation Spec v0.5.0.

| Channel | Score | Grade | Passed | Failed | Skipped |
| --- | ---: | --- | ---: | ---: | ---: |
| GitHub Pages | 96 | A | 19 | 1 | 3 |
| Railway | 98 | A | 19 | 1 | 3 |

Railway passed content negotiation: every sampled page returned Markdown for
`Accept: text/markdown`. Pages returned HTML because static hosting cannot vary
one URL by request headers. Pages passed cache-header hygiene while Railway did
not, so the score does not make the live channel uniformly better.

The regression lane and baseline procedure are covered in
[[docs/operations/agent-readiness-score|Continuous agent-readiness scoring]].

## What both channels provide

The shared freeze and export layer gives both channels HTML pages, `.md`
sidecars, `llms.txt`, and bulk machine-readable artifacts such as `catalog.json`,
`search.json`, `semantic.json`, and `tools.json`. Pages is particularly good at
these immutable snapshots: it is CDN-friendly, needs no healthy application
process, and lets an agent download the complete corpus for local processing.

## What requires a live runtime

Some capabilities need code to run for each request.

| Capability | Pages | Railway |
| --- | --- | --- |
| `Accept: text/markdown` | Returns HTML | Returns Markdown |
| Filtered `/catalog/query.json` | No route | Executes graph filters and pagination |
| `/search/semantic?q=` | Publishes the index only | Returns ranked hybrid results |
| MCP | Publishes `tools.json` metadata | Can run a separate Furatena MCP process |

The current Railway web service does not expose an MCP transport. MCP is a
live-only runtime capability because a static host cannot execute tools or serve
resources, but it still requires a deployed `fura mcp` process and an appropriate
security boundary.

## Live does not yet mean no-redeploy publishing

Production Railway runs in preview mode from a catalog frozen into its Docker
image. It can execute queries over that snapshot, but it does not fetch changed
git content while the process is running.

Author and hybrid modes can watch files already present under a content root.
Git-backed mounts can fetch and atomically promote a snapshot when the catalog is
constructed. There is not yet a runtime trigger that performs another fetch and
swaps the loaded registry. For now, a content commit needs a new image deployment
to reach Railway.

## Channel recommendation

Publish both channels from every reviewed release. Make Railway the default for
interactive agent use, including negotiated Markdown, filtered graph queries,
and semantic search. Keep Pages as the browser-facing and bulk-artifact fallback.

Two constraints qualify that recommendation:

- deploy the response-streaming fix tracked by issue #329 before routing large
  identity-encoded catalog downloads to Railway by default; and
- complete issue #318 before promising content re-sync without a deploy.

Until an MCP transport is deployed and secured, describe Railway as the live
HTTP query channel and `fura mcp` as a separate runtime rather than presenting
the current web service as an MCP endpoint.
