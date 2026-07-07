---
title: Adoption research
description: Personas, competitive context, and success metrics for packaging Furatena.
draft: false
weight: 70
lang: en
type: doc
tags: [research, adoption, personas, metrics]
category: concepts
---

# Adoption research

Research snapshot: **2026-07-02**.

Furatena's packaging bet is that one source corpus can serve local authoring, static
publishing, live apps, PDFs, search, and agent access without forcing users into a
hosted-only docs platform.

## Personas

| Persona | Job | Current pain | Furatena promise | Validation question |
|---------|-----|--------------|------------------|---------------------|
| Solo founder | Ship docs without operating a docs team | Hosted docs are fast but can lock content and pricing to a platform | `uv run fura serve`, static export, and agent metadata from the repo | Can a founder publish a useful docs site in under 30 minutes from existing markdown? |
| DevRel team | Keep API guides, examples, and release docs current | API docs, changelog, snippets, and AI answers drift across tools | One catalog graph for prose, API refs, releases, PDFs, search, and MCP | Does one graph reduce duplicated launch work for a release? |
| Platform docs team | Migrate mixed docs corpora and govern quality | Docusaurus, Sphinx, MyST, MDX, and generated refs create migration risk | Migration reports, mixed-format adapters, link lint, and author dashboard | Can the team identify blockers before changing source files? |
| Enterprise admin | Approve private docs, deployment, and agent access | Hosted platforms require security review; self-hosted systems need observability | Local/static/cloud/self-hosted profiles plus author-only private surfaces | Can admins reason about what is public, private, exported, and agent-readable? |

## Competitive map

| Segment | Examples | Observed strengths | Opening for Furatena |
|---------|----------|--------------------|----------------------|
| Static-site docs | Docusaurus, VitePress | Mature static publishing, large plugin/theme ecosystems, easy GitHub/CDN hosting | Win migrations where static output is required but teams also need local authoring, PDF, agent metadata, and graph checks from the same corpus |
| Hosted docs platforms | [Fern](https://buildwithfern.com/learn/docs/getting-started/overview), [Mintlify](https://www.mintlify.com/docs/quickstart), [ReadMe](https://docs.readme.com/main/docs/about-readme), GitBook | Fast onboarding, polished hosted UX, API references, analytics, AI/search features, collaborative editing | Offer repo-owned content, no-cloud local authoring, static exports, and explicit self-hosted enterprise profile |
| Sphinx and MyST ecosystems | [Sphinx](https://www.sphinx-doc.org/en/master/usage/quickstart.html), [MyST](https://mystmd.org/guide) | Rich technical writing semantics, cross-references, inventories, PDF/book/scientific outputs | Preserve RST/MyST semantics while projecting into modern hypermedia, search, PDF, and MCP surfaces |
| API developer portals | Fern, ReadMe, [Redocly CLI](https://redocly.com/docs/cli), Redocly Realm | OpenAPI import, API governance, reference layouts, SDK/snippet workflows | Make API docs first-class catalog nodes with graph/search/MCP contracts instead of a separate portal silo |
| Agent-native docs | Fern AI features, Mintlify `llms.txt`, MCP and OpenAPI-to-MCP research | Vendors are making docs consumable by agents and exposing markdown/LLM indexes | Differentiate with source-aware MCP tools, stale/private safety, graph-native diagnostics, and local-first agent workflows |

## Research plan

Interview six to eight users per persona, using their real docs corpus when possible.
Pair interviews with a timed import exercise so answers are anchored in behavior.

Core tasks:

1. Start from an existing docs repo and run `fura migrate --report --json`.
2. Open `fura serve --author` and use `/docs/_author/dashboard` to find blockers.
3. Freeze/export static output and inspect `/channels.json`, `/deployment-profiles.json`, and `/tools.json`.
4. Run one search task and one agent retrieval task against a known answer.
5. Ask the user which output channel they would ship first and what would block approval.

Probe by persona:

| Persona | Questions |
|---------|-----------|
| Solo founder | Which path is faster: hosted docs import or local repo export? What would make you pay? |
| DevRel team | Which release artifacts are duplicated today? What must be reviewed before launch? |
| Platform docs team | Which constructs block migration? Which diagnostics are actionable enough for a backlog? |
| Enterprise admin | What evidence is needed for public/private boundaries, auditability, and self-hosting approval? |

## Success metrics

| Area | Metric | Target for adoption readiness |
|------|--------|-------------------------------|
| Activation | Time from clone to local author server with at least one edited page | Median under 10 minutes for solo users; under 30 minutes for imported repos |
| Migration completion | Share of indexed pages with no blocking migration/check errors | 90% of pages clean after first automated pass; remaining blockers grouped by owner/source |
| Build scale | Time to check, freeze, export, and generate PDFs for representative corpora | P95 fits CI budgets: check under 5 minutes, static export under 10 minutes, PDF batch documented separately |
| Search usefulness | Known-answer retrieval rate for prose, API refs, and release docs | 80% top-3 retrieval before tuning; 90% after metadata cleanup |
| Agent readiness | MCP eval pass rate and stale/private safety warnings | Zero private leaks, zero agent errors, warnings tied to explicit remediation |
| Buyer confidence | Deployment-profile fit and approval blockers | User can choose local/static/cloud/self-hosted profile and name remaining approval risks in one session |

## Repeatable activation protocol

Activation measurements are explicit and local. Start a separate session for a
new site or imported repository, mark the milestones, then aggregate only the
sanitized duration report:

```bash
fura activation start --journey new-site --session /tmp/new-site.json --consent --json
fura activation mark --session /tmp/new-site.json --event first-edit --json
fura activation mark --session /tmp/new-site.json --event first-publish --automated-seconds 30 --manual-seconds 90 --json
fura activation report --session /tmp/new-site.json --output activation-report.json --json
```

Imported-repository sessions use `--journey imported-site` and also mark
`clean-migration`. The report keeps new and imported paths separate, reports
median and P95 milestone durations, and totals automated versus manual
remediation time. The first-edit readiness targets remain 10 minutes for new
sites and 30 minutes for imports. First-publish and clean-migration times are
measured without inventing a target before pilot evidence exists.

Collection is opt-in: omitting `--consent` fails without writing a session.
Sessions stay on the local filesystem and the shareable report contains no
source content, source paths, actor/host identity, wall-clock timestamps, or
session identifiers. Furatena performs no network transmission for this
protocol.

## Reproducible readiness decision

The beta scorecard turns the success metrics above into 16 fixed gates. Its
versioned JSON input covers activation, migration, build, retrieval, agent
safety, and buyer confidence. Every area names an owner and remediation, while
unavailable evidence is represented as `null` and produces a no-go result.

```bash
fura scorecard \
  --input adoption-evidence.json \
  --output adoption-scorecard.json \
  --json
```

The output records policy version `1.0.0`, the decision date, and a SHA-256 of
the canonical evidence manifest. This makes two decisions comparable without
depending on file paths, filesystem timestamps, or process state. See
[[docs/concepts/adoption-readiness-scorecard|Beta adoption-readiness scorecard]]
for the complete input contract and current decision.

## Positioning

Lead with **repo-owned docs that become many surfaces**. Hosted platforms win on
polish and managed workflows; static generators win on simplicity; Sphinx/MyST win on
technical semantics. Furatena should compete where teams need all three: local control,
rich migration diagnostics, and machine-readable surfaces for humans, CI, and agents.

The adoption package is ready to test when a user can answer:

- What can I ship publicly?
- What is stale or blocked?
- Which output channel fits this audience?
- What can an agent safely read or do?
