---
title: Persona-by-Diataxis coverage matrix
description: Documentation coverage, ownership, priority, and backlog seeds by audience and product surface.
weight: 75
lang: en
type: doc
tags: [documentation, personas, diataxis, coverage, roadmap]
category: concepts
---

# Persona-by-Diataxis coverage matrix

This matrix turns the implementation-derived [public surface inventory](/docs/reference/public-surface-inventory/)
into documentation work for the four target personas identified in
[adoption research](/docs/concepts/adoption-research/).

## Status contract

- **Existing** — the quadrant has a usable, persona-relevant path today.
- **Partial** — useful material exists, but the task, audience, or contract is incomplete.
- **Missing** — no sufficient path exists. Every P0/P1 gap names an accountable owner and backlog seed.
- **Intentionally internal** — exposing the implementation detail would not help this persona; the cell records why.

Owners are durable workstreams rather than individual people: `docs-product`,
`devrel`, `platform-docs`, `agent-platform`, and `security-operations`.

## Inventory evidence

| Surface group | Inventory source | Current evidence |
|---|---|---|
| CLI | `cli_command` | 33 commands; all identifier-linked |
| Routes | `route` | 46 routes; 38 linked, 8 explicitly internal or deferred with reasons |
| Configuration | `config_field` | 236 fields; all identifier-linked |
| MCP | `mcp_tool`, `mcp_resource` | 16 tools and 10 stable resources; all identifier-linked |
| Sidecars | `sidecar` | 25 machine-readable/live outputs; all identifier-linked |
| Diagnostics | `diagnostic` | 56 rule ids/families; all identifier-linked |
| Deployment | `deployment_profile` | Four supported profiles; all identifier-linked |

Identifier linkage is evidence that a surface is mentioned, not proof that all
four learning modes are complete. The matrix below supplies that qualitative
review.

## Coverage matrix

| Persona | Surface | Tutorial | How-to | Explanation | Reference | Backlog seed |
|---|---|---|---|---|---|---|
| `solo-founder` | `cli` | Partial — [Quickstart](/docs/get-started/quickstart/) starts the server but does not reach first deploy | Partial — CLI page covers common commands, not complete tasks | Existing — [Platform proof](/docs/concepts/platform-proof/) explains one-corpus workflows | Existing — generated reference covers every command, option, and default | #169 |
| `solo-founder` | `routes` | Intentionally internal — route registration is not an onboarding goal | Partial — navigation tasks omit recovery from dead ends | Existing — [Hypermedia model](/docs/concepts/hypermedia-model/) explains route behavior | Existing — 38 routes have direct coverage and 8 implementation/deferred routes have reasoned exemptions | #168, #172, #173 |
| `solo-founder` | `configuration` | Partial — standalone tutorial introduces only the minimum config | Partial — docs.yaml and mounts.yaml cover common edits | Partial — configuration layering is split across theming and federation | Existing — generated reference covers every field, type, requirement, and default | #171 |
| `solo-founder` | `mcp` | Missing P1 — owner: `docs-product`; no first successful local MCP tutorial | Missing P1 — owner: `agent-platform`; no task guide from connection to safe retrieval | Partial — agent workflow concepts explain boundaries | Existing — every tool, schema, stable resource, and policy boundary is indexed | #170, #172 |
| `solo-founder` | `sidecars` | Partial — platform proof introduces the main files | Missing P1 — owner: `agent-platform`; no consumption recipe by use case | Partial — DCP explains graph outputs but not selection guidance | Existing — every sidecar URL and response shape is indexed | #170, #172 |
| `solo-founder` | `diagnostics` | Partial — quickstart includes `fura check` | Partial — check-and-lint covers common remediation | Partial — lifecycle and visibility rationale is distributed | Existing — every stable rule id and family is indexed with first response | #172, #173 |
| `solo-founder` | `deployment` | Missing P0 — owner: `docs-product`; no verified first GitHub Pages tutorial | Partial — deploy guide lacks complete failure recovery | Existing — deployment profiles explain tradeoffs | Existing — profile requirements, defaults, and failure boundaries are consolidated | #169, #172 |
| `devrel-team` | `cli` | Partial — quickstart is solo-oriented, not release-oriented | Partial — release, API diff, and author tasks are split | Existing — platform proof explains shared projections | Existing — parser-derived command, option, and default coverage is exhaustive | #170 |
| `devrel-team` | `routes` | Intentionally internal — registrar internals are not a launch workflow | Partial — API/reference and release journeys lack end-to-end checks | Partial — graph/route concepts exist without launch framing | Existing — all routes are directly covered or explicitly internal/deferred | #168, #172, #173 |
| `devrel-team` | `configuration` | Partial — sample config does not model release/API sites | Partial — theming and navigation recipes exist separately | Partial — delivery-head and composition rationale is incomplete | Existing — generated docs.yaml and mounts.yaml field contracts are exhaustive | #170 |
| `devrel-team` | `mcp` | Missing P1 — owner: `devrel`; no API-release-to-MCP tutorial | Missing P1 — owner: `agent-platform`; no release validation/consumption guide | Partial — agent workflows explain safety and graph access | Existing — tools, resources, schemas, diagnostics, and access policy share one contract | #170, #172 |
| `devrel-team` | `sidecars` | Partial — platform proof names agent outputs | Missing P1 — owner: `devrel`; no release artifact consumption guide | Existing — one graph/many projections is explained | Existing — URLs, media shapes, schemas, and compatibility expectations are consolidated | #170, #172 |
| `devrel-team` | `diagnostics` | Partial — validation appears in migration and author flows | Partial — API diff and check remediation exist separately | Partial — severity and lifecycle concepts exist | Existing — the complete stable diagnostic catalog is indexed | #172, #173 |
| `devrel-team` | `deployment` | Missing P1 — owner: `docs-product`; no release-site Pages tutorial | Partial — deploy guide lacks launch rollback/troubleshooting | Existing — deployment profiles cover channel choices | Existing — requirements, publishing, output, and failure contracts are consolidated | #169, #172 |
| `platform-docs-team` | `cli` | Partial — migration tutorial starts the workflow | Partial — migrate, check, author, and export guides are disconnected | Partial — command roles are explained across concepts | Existing — parser-derived reference is generated and drift-gated | #170 |
| `platform-docs-team` | `routes` | Intentionally internal — implementation routes are not the migration entry point | Partial — journey/dead-end validation is absent | Existing — route and graph architecture is documented | Existing — all routes are directly covered or carry reviewed internal/deferred reasons | #168, #172, #173 |
| `platform-docs-team` | `configuration` | Partial — project layout covers initial files | Partial — federation, theming, and deployment recipes exist | Partial — composition and delivery selection need a unified model | Existing — exhaustive fields, types, requirements, defaults, and sources are generated | #171 |
| `platform-docs-team` | `mcp` | Missing P1 — owner: `platform-docs`; no migrated-corpus MCP tutorial | Missing P1 — owner: `agent-platform`; no source-health/stale repair runbook | Partial — graph, RBAC, and source concepts exist | Existing — tools, schemas, resources, and access boundaries share one integrator reference | #170, #172 |
| `platform-docs-team` | `sidecars` | Partial — freeze/export introduces outputs | Partial — deployment and agent docs cover selected files | Partial — DCP and dual IR explain representation | Existing — all schemas, URLs, and compatibility expectations are consolidated | #170, #172 |
| `platform-docs-team` | `diagnostics` | Partial — migration report tutorial surfaces blockers | Partial — check-and-lint gives common actions | Partial — diagnostics-first migration rationale exists | Existing — all rule ids/families and first responses are indexed | #172, #173 |
| `platform-docs-team` | `deployment` | Missing P1 — owner: `docs-product`; no migration-to-first-deploy tutorial | Partial — profiles and deploy steps are separate | Existing — deployment tradeoffs are explicit | Existing — source-provider health states and recovery boundaries are explicit | #169, #172 |
| `enterprise-admin` | `cli` | Partial — no admin-oriented validation walkthrough | Partial — security commands are distributed across RBAC and agent docs | Partial — local trust and remote policy are explained | Existing — security-relevant options and defaults are included in exhaustive generated coverage | #170 |
| `enterprise-admin` | `routes` | Intentionally internal — route implementation is not an approval artifact | Partial — public/private inspection tasks exist without a review checklist | Partial — RBAC and threat model explain boundaries | Existing — exposed routes are covered and implementation-only/deferred routes are reasoned | #172, #173 |
| `enterprise-admin` | `configuration` | Partial — profiles introduce deployment settings | Partial — tenancy and access recipes are incomplete | Partial — identity/delivery concepts exist across RBAC and profiles | Existing — identity, delivery, and source fields share one generated contract | #172 |
| `enterprise-admin` | `mcp` | Missing P0 — owner: `security-operations`; no secure remote MCP approval tutorial | Missing P0 — owner: `agent-platform`; no privileged-token/audit operations runbook | Partial — RBAC and threat model explain trust boundaries | Existing — complete tools, resources, schemas, access, rate, token, and audit contracts are consolidated | #170, #172 |
| `enterprise-admin` | `sidecars` | Partial — profiles identify public outputs | Missing P1 — owner: `security-operations`; no public-output inspection runbook | Partial — visibility filtering and channel intent are explained | Existing — schemas, URLs, and anonymous filtering guarantees are consolidated | #170, #172 |
| `enterprise-admin` | `diagnostics` | Partial — checks appear in approval workflows | Partial — lifecycle/security remediation is distributed | Partial — threat model explains why failures block | Existing — diagnostic severity, rule families, and response guidance are consolidated | #172, #173 |
| `enterprise-admin` | `deployment` | Missing P0 — owner: `security-operations`; no approval-oriented self-hosted tutorial | Partial — deployment profiles lack incident/failure runbooks | Existing — local/static/cloud/enterprise tradeoffs are explicit | Existing — auth, sync, observability, publishing, and failure states are consolidated | #169, #172 |

## Backlog routing

| Issue | Matrix work it owns |
|---|---|
| #167 | Progressive adopt, author, publish, operate, and integrate navigation |
| #168 | Persona journey and dead-end validation |
| #169 | Verified first GitHub Pages deploy and recovery tutorial |
| #170 | Author lifecycle, MCP, agent-output, and migration task guides |
| #171 | Generated CLI/configuration reference and drift checks |
| #172 | MCP, sidecar, diagnostic, access, and deployment reference |
| #173 | Orphan, navigation, link, and public-feature coverage gates |
| #174 | Executable snippets plus owner/review freshness metadata |

This routing makes partial coverage actionable without manufacturing one ticket
per inventory identifier. The machine inventory remains the exact surface list;
this matrix owns audience, learning mode, priority, and workstream accountability.
