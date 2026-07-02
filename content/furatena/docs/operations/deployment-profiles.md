---
title: Deployment profiles
description: Local, static, cloud, and self-hosted operating paths
draft: false
weight: 45
lang: en
type: doc
tags: [deploy, profiles, mcp, enterprise]
category: operations
---

Furatena starts as a local repo tool and scales into static, live, and governed
deployments without changing the content model. Use deployment profiles to choose
the smallest operating shape that fits the audience.

## Profile summary

| Profile | Best for | Publishing |
|---------|----------|------------|
| **Local authoring** | Solo authors and fast product-doc edits | None until freeze/export |
| **Static publishing** | GitHub Pages, CDNs, immutable public docs | `app/public/` |
| **Cloud live app** | Team previews, live search, app-hosted MCP | Live Chirp routes |
| **Self-hosted enterprise** | Private multi-team docs and governance | Reviewed live/static/PDF/agent channels |

The default remains **local authoring**: `uv run fura serve` reads the checkout,
indexes local content, and exposes author tools only from the local process.

## Local authoring

Required services:

- Python runtime
- local filesystem
- browser

Storage is the repository checkout, with `app/frozen/` only when preview snapshots
are useful. Auth is local-process trust: do not expose author routes directly to
the public internet.

Happy path:

```bash
uv sync
uv run fura serve
```

Tradeoff: this is the safest and fastest loop, but it is not a deployment until
you freeze or export public artifacts.

## Static publishing

Required services:

- Python build runner
- static host, CDN, or object storage

Storage is split between `app/frozen/` for portable catalog IR and `app/public/`
for the deployable site. Public export excludes protected, draft, and private
content.

Happy path:

```bash
export FURA_BASE_URL=https://example.com/docs
uv run fura freeze
uv run fura export
```

Tradeoff: static output has the lowest operational burden, but no live authoring
or privileged MCP tools.

## Cloud live app

Required services:

- Python app service
- reverse proxy
- git or filesystem source mount

Storage is the mounted content source plus an optional frozen cache for fast
startup. Put the app behind deployment auth before exposing author routes.

Happy path:

```bash
export FURA_BASE_URL=https://docs.example.com
uv run fura serve
```

Tradeoff: live app and deployed MCP surfaces need service health, access control,
and source-sync operations.

## Self-hosted enterprise

Required services:

- Python app service
- identity provider or gateway auth
- source sync
- observability

Storage is tenant-scoped source mounts, frozen/public artifacts, and audit/report
outputs. Sensitive author MCP tools require trusted sessions and privileged tokens.

Happy path:

```bash
uv run fura check --agent --json
uv run fura mcp --author --include-private
```

Tradeoff: this profile supports private content, governance, and multi-team scale,
but it carries the most operational responsibility.

## Machine-readable profile contract

`/deployment-profiles.json` exposes the same profiles for agents, deployment
tooling, and product packaging. Static exports include it beside `channels.json`
so static-only deployments can still advertise their supported operating model.
