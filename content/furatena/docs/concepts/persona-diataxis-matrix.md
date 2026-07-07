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
| CLI | `cli_command` | 31 commands; all identifier-linked |
| Routes | `route` | 46 routes; 35 linked, 11 missing |
| Configuration | `config_field` | 96 fields; 29 linked, 67 missing |
| MCP | `mcp_tool`, `mcp_resource` | 16 tools and 10 stable resources; 23 linked, 3 missing |
| Sidecars | `sidecar` | 25 machine-readable/live outputs; all identifier-linked |
| Diagnostics | `diagnostic` | 45 rule ids/families; 3 linked, 42 missing |
| Deployment | `deployment_profile` | Four supported profiles; all identifier-linked |

Identifier linkage is evidence that a surface is mentioned, not proof that all
four learning modes are complete. The matrix below supplies that qualitative
review.

## Coverage matrix

| Persona | Surface | Tutorial | How-to | Explanation | Reference | Backlog seed |
|---|---|---|---|---|---|---|
| `solo-founder` | `cli` | Partial — [Quickstart](/docs/get-started/quickstart/) starts the server but does not reach first deploy | Partial — CLI page covers common commands, not complete tasks | Existing — [Platform proof](/docs/concepts/platform-proof/) explains one-corpus workflows | Partial — options/defaults are incomplete | #169, #171 |
| `solo-founder` | `routes` | Intentionally internal — route registration is not an onboarding goal | Partial — navigation tasks omit recovery from dead ends | Existing — [Hypermedia model](/docs/concepts/hypermedia-model/) explains route behavior | Partial — public URLs exist across several pages, not one contract | #168, #172 |
| `solo-founder` | `configuration` | Partial — standalone tutorial introduces only the minimum config | Partial — docs.yaml and mounts.yaml cover common edits | Partial — configuration layering is split across theming and federation | Partial — 67 fields lack identifier-linked coverage | #171 |
| `solo-founder` | `mcp` | Missing P1 — owner: `docs-product`; no first successful local MCP tutorial | Missing P1 — owner: `agent-platform`; no task guide from connection to safe retrieval | Partial — agent workflow concepts explain boundaries | Partial — tools are listed but stable resource contracts are incomplete | #170, #172 |
| `solo-founder` | `sidecars` | Partial — platform proof introduces the main files | Missing P1 — owner: `agent-platform`; no consumption recipe by use case | Partial — DCP explains graph outputs but not selection guidance | Partial — URLs exist across multiple references | #170, #172 |
| `solo-founder` | `diagnostics` | Partial — quickstart includes `fura check` | Partial — check-and-lint covers common remediation | Partial — lifecycle and visibility rationale is distributed | Missing P1 — owner: `platform-docs`; stable rule-id reference is absent | #172, #173 |
| `solo-founder` | `deployment` | Missing P0 — owner: `docs-product`; no verified first GitHub Pages tutorial | Partial — deploy guide lacks complete failure recovery | Existing — deployment profiles explain tradeoffs | Partial — workflow/base-path defaults are scattered | #169, #172 |
| `devrel-team` | `cli` | Partial — quickstart is solo-oriented, not release-oriented | Partial — release, API diff, and author tasks are split | Existing — platform proof explains shared projections | Partial — command/options coverage is incomplete | #170, #171 |
| `devrel-team` | `routes` | Intentionally internal — registrar internals are not a launch workflow | Partial — API/reference and release journeys lack end-to-end checks | Partial — graph/route concepts exist without launch framing | Partial — 11 public routes lack direct coverage | #168, #172 |
| `devrel-team` | `configuration` | Partial — sample config does not model release/API sites | Partial — theming and navigation recipes exist separately | Partial — delivery-head and composition rationale is incomplete | Partial — public config fields are not exhaustive | #170, #171 |
| `devrel-team` | `mcp` | Missing P1 — owner: `devrel`; no API-release-to-MCP tutorial | Missing P1 — owner: `agent-platform`; no release validation/consumption guide | Partial — agent workflows explain safety and graph access | Partial — tool coverage is strong; resource and diagnostics contracts lag | #170, #172 |
| `devrel-team` | `sidecars` | Partial — platform proof names agent outputs | Missing P1 — owner: `devrel`; no release artifact consumption guide | Existing — one graph/many projections is explained | Partial — schemas and compatibility expectations are distributed | #170, #172 |
| `devrel-team` | `diagnostics` | Partial — validation appears in migration and author flows | Partial — API diff and check remediation exist separately | Partial — severity and lifecycle concepts exist | Missing P1 — owner: `platform-docs`; no complete diagnostic catalog | #172, #173 |
| `devrel-team` | `deployment` | Missing P1 — owner: `docs-product`; no release-site Pages tutorial | Partial — deploy guide lacks launch rollback/troubleshooting | Existing — deployment profiles cover channel choices | Partial — CI and output-channel contract is split | #169, #172 |
| `platform-docs-team` | `cli` | Partial — migration tutorial starts the workflow | Partial — migrate, check, author, and export guides are disconnected | Partial — command roles are explained across concepts | Partial — parser-derived command reference is not generated | #170, #171 |
| `platform-docs-team` | `routes` | Intentionally internal — implementation routes are not the migration entry point | Partial — journey/dead-end validation is absent | Existing — route and graph architecture is documented | Partial — handler/public URL contract is incomplete | #168, #172 |
| `platform-docs-team` | `configuration` | Partial — project layout covers initial files | Partial — federation, theming, and deployment recipes exist | Partial — composition and delivery selection need a unified model | Missing P0 — owner: `platform-docs`; exhaustive fields/defaults/reference is absent | #171 |
| `platform-docs-team` | `mcp` | Missing P1 — owner: `platform-docs`; no migrated-corpus MCP tutorial | Missing P1 — owner: `agent-platform`; no source-health/stale repair runbook | Partial — graph, RBAC, and source concepts exist | Partial — tools/resources are not one integrator reference | #170, #172 |
| `platform-docs-team` | `sidecars` | Partial — freeze/export introduces outputs | Partial — deployment and agent docs cover selected files | Partial — DCP and dual IR explain representation | Partial — schemas, URLs, and compatibility are split | #170, #172 |
| `platform-docs-team` | `diagnostics` | Partial — migration report tutorial surfaces blockers | Partial — check-and-lint gives common actions | Partial — diagnostics-first migration rationale exists | Missing P0 — owner: `platform-docs`; rule ids, ownership, and remedies are incomplete | #172, #173 |
| `platform-docs-team` | `deployment` | Missing P1 — owner: `docs-product`; no migration-to-first-deploy tutorial | Partial — profiles and deploy steps are separate | Existing — deployment tradeoffs are explicit | Partial — source-provider failure and recovery reference is incomplete | #169, #172 |
| `enterprise-admin` | `cli` | Partial — no admin-oriented validation walkthrough | Partial — security commands are distributed across RBAC and agent docs | Partial — local trust and remote policy are explained | Partial — security-relevant options/defaults are incomplete | #170, #171 |
| `enterprise-admin` | `routes` | Intentionally internal — route implementation is not an approval artifact | Partial — public/private inspection tasks exist without a review checklist | Partial — RBAC and threat model explain boundaries | Partial — author and public route exposure is not consolidated | #172 |
| `enterprise-admin` | `configuration` | Partial — profiles introduce deployment settings | Partial — tenancy and access recipes are incomplete | Partial — identity/delivery concepts exist across RBAC and profiles | Missing P1 — owner: `security-operations`; identity, delivery, and source fields lack one reference | #171, #172 |
| `enterprise-admin` | `mcp` | Missing P0 — owner: `security-operations`; no secure remote MCP approval tutorial | Missing P0 — owner: `agent-platform`; no privileged-token/audit operations runbook | Partial — RBAC and threat model explain trust boundaries | Missing P0 — owner: `agent-platform`; complete tools/resources/access contract is absent | #170, #172 |
| `enterprise-admin` | `sidecars` | Partial — profiles identify public outputs | Missing P1 — owner: `security-operations`; no public-output inspection runbook | Partial — visibility filtering and channel intent are explained | Missing P1 — owner: `agent-platform`; schemas, URLs, and filtering guarantees are fragmented | #170, #172 |
| `enterprise-admin` | `diagnostics` | Partial — checks appear in approval workflows | Partial — lifecycle/security remediation is distributed | Partial — threat model explains why failures block | Missing P0 — owner: `security-operations`; diagnostic severity, ownership, and response reference is absent | #172, #173 |
| `enterprise-admin` | `deployment` | Missing P0 — owner: `security-operations`; no approval-oriented self-hosted tutorial | Partial — deployment profiles lack incident/failure runbooks | Existing — local/static/cloud/enterprise tradeoffs are explicit | Partial — auth, sync, observability, and failure states need consolidation | #169, #172 |

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
