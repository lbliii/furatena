---
title: Integrator and operations reference
owner: agent-platform
reviewed_at: "2026-07-07"
description: MCP, sidecar, diagnostics, access, deployment, and source-health contracts.
weight: 40
lang: en
type: doc
category: reference
tags: [mcp, sidecars, diagnostics, access, deployment, operations]
---

# Integrator and operations reference

This is the exhaustive contract index for software that consumes or operates a
Furatena catalog. JSON payloads use `schema_version: 1` unless the row names a
different version. URLs are root-relative before `FURA_BASE_PATH` is applied.
Run `fura check --agent --json` after changing an MCP or sidecar contract.

## MCP transport and policy

`fura mcp` serves MCP over stdio using protocol version `2025-06-18`. Every tool
result contains MCP text content and `structuredContent`; callers should consume
the structured object. `fura mcp --describe --json` returns the live schemas.

Local sessions default to an admin-equivalent process subject. Remote sessions
default to `anonymous`, apply actor, tenant, burst, and sensitive-tool limits plus
`--timeout` and `--max-output-chars`, and require a configured
`--privileged-token` for sensitive author tools. Author tools require
`--author --include-private`; writes default to `dry_run=true` and require both
`dry_run=false` and `confirmed=true`.

### Tools

“Required input” and “input properties” are the active JSON Schema names.
“Required output” lists the stable required keys in `structuredContent`.

| Tool | Required input | Input properties | Required output | Contract |
|---|---|---|---|---|
| `semantic_search` | `query` | `edition`, `limit`, `mount`, `query`, `tag`, `url_prefix` | `query`, `ranking`, `filters`, `count`, `results` | Hybrid keyword and semantic retrieval; `limit` is 1–50 and filters are echoed in the result. |
| `retrieve_node` | `node_id` | `node_id` | `node_id`, `chunks`, `backlinks`, `api_operation` | Retrieve one accessible catalog node and its context. |
| `query_graph` | none | `edge`, `edge_kind`, `format`, `from`, `include_private`, `kind`, `lang`, `link_edge`, `linked_from`, `linked_to`, `locale`, `mount`, `owner`, `source`, `tag`, `target`, `team`, `to` | `query`, `page_count`, `edge_count`, `pages`, `edges`, `graph_nodes` | Filter pages and DCP edges; private inclusion is bounded by session policy. |
| `traverse_graph` | none | `direction`, `limit`, `node_id`, `url` | `node`, `direction`, `results` | Traverse `neighbors`, `backlinks`, `children`, or `outbound`; limit is 1–100. |
| `inspect_source_health` | none | `mount` | `mount_count`, `mounts` | Return source sync, index, file, page, and channel health per mount. |
| `run_checks` | none | none | `ok`, `errors`, `warnings` | Run content, link, schema, theme, and view checks. |
| `explain_stale_impact` | none | `slug` | `stale_count`, `entries`, `impact`, `repair_tasks`, `task_markdown` | Explain stale graph/search/export impact and produce repair tasks. |
| `author_create_draft` | `slug` | `actor`, `confirmed`, `dry_run`, `mount`, `privileged_token`, `slug`, `title` | `operation_id`, `ok`, `audit` | Create a draft; confirmed non-dry-run is required to write. |
| `author_read_source` | `target` | `actor`, `mount`, `privileged_token`, `target` | `operation_id`, `ok`, `source`, `source_revision`, `audit` | Read source in an include-private author session. |
| `author_propose_edit` | `target`, `old_text`, `new_text` | `actor`, `confirmed`, `dry_run`, `mount`, `new_text`, `old_text`, `privileged_token`, `source_revision`, `target` | `operation_id`, `ok`, `diff`, `audit` | Always preview an exact-text edit without writing. |
| `author_apply_edit` | `target`, `old_text`, `new_text` | `actor`, `confirmed`, `dry_run`, `mount`, `new_text`, `old_text`, `privileged_token`, `source_revision`, `target` | `operation_id`, `ok`, `diff`, `audit` | Apply an exact-text edit with revision and confirmation guards. |
| `author_validate` | none | `actor`, `mount`, `privileged_token`, `target` | `ok`, `errors`, `warnings`, `audit` | Validate all content or one source target. |
| `author_publish` | `target` | `actor`, `confirmed`, `dry_run`, `mount`, `privileged_token`, `source_revision`, `target` | `operation_id`, `ok`, `audit` | Transition draft to public with publication impact. |
| `author_unpublish` | `target` | `actor`, `confirmed`, `dry_run`, `mount`, `privileged_token`, `source_revision`, `target` | `operation_id`, `ok`, `audit` | Transition public content back to draft. |
| `author_archive` | `target` | `actor`, `confirmed`, `dry_run`, `mount`, `privileged_token`, `source_revision`, `target` | `operation_id`, `ok`, `audit` | Archive content and remove it from public output. |
| `author_inspect_publication_impact` | `target` | `actor`, `mount`, `privileged_token`, `target` | `ok`, `status`, `validation`, `stale_impact`, `audit` | Read lifecycle, validation, and stale impact before transition. |

### Resources

All stable resources return `application/json`. Node-specific resources use
`fura://catalog/nodes/{url-encoded-node-id}` and the same access filtering as
`retrieve_node`.

| URI | Payload contract |
|---|---|
| `fura://catalog/nodes` | `count`, `nodes`; every node is filtered by retrieve permission. |
| `fura://catalog/graph` | DCP catalog graph: `pages`, `edges`, `graph_nodes`, and namespaces. |
| `fura://catalog/api-operations` | API operation records and safe try-it metadata. |
| `fura://catalog/structure` | Content-IR headings and directives. |
| `fura://catalog/inventories` | Reference-inventory manifest and stable inventory URLs. |
| `fura://catalog/sources` | Source-health payload documented below. |
| `fura://catalog/channels` | Mount channel metadata and active channel. |
| `fura://reports/validation` | `ok`, `errors`, and `warnings` diagnostic objects. |
| `fura://reports/stale-impact` | Stale entries, graph/output impact, and repair tasks. |
| `fura://reports/audit` | Retained sanitized calls plus backend metadata: event/correlation identity, actor, tenant, site, action/tool, target, inputs, outcome/status, duration, and author transition fields. Secrets and authored/query content are redacted. |

### Durable audit storage

Pass `--audit-store PATH` to persist MCP audit events as permission-restricted
JSONL that survives process restarts. `--audit-retention-days N` defaults to 90;
expired records are removed before reads and exports. Without `--audit-store`, the
same contract uses a thread-safe process-local implementation suitable for tests
and local sessions.

Every persisted event has `event_id`, `timestamp`, `correlation_id`, `actor`,
`tenant`, `site`, `action`, `target`, and `outcome`. Tool-specific metadata is
retained, but token, credential, authorization, password, and cookie fields are
replaced with `<redacted>`. Query, prompt, body, source, diff, patch, and edit-text
fields are replaced with `<redacted:content>` before either backend receives the
event. The provider-neutral `AuditStore` boundary supports append, tenant/time
queries, retention purge, structured export, and writing an export snapshot.
`fura://reports/audit` exposes the active backend, retention, count, entries, and
session policy; `fura mcp --describe --json` reports the empty/current store
metadata before serving.

### Shared abuse controls

`--rate-limit-store PATH` moves fixed-window counters from the thread-safe local
backend into a restart-safe SQLite store. Each decision updates all applicable
buckets in one `BEGIN IMMEDIATE` transaction, so restarts, threads, and workers
sharing the path cannot reset or race the limits. Tenant and actor identities are
hashed before persistence.

The default policy combines `--rate-limit-burst` per actor per second,
`--rate-limit` per actor per minute, `--tenant-rate-limit` across tenant actors
per minute, and `--sensitive-rate-limit` across sensitive author tools per actor
per minute. A denied response includes the violated rule, backend, and retry
delay in `rate_limit`; its audit event has outcome `rate_limited`.

`--rate-limit-fallback deny` is the default for a configured shared backend and
fails closed when an atomic decision cannot be made. The explicit `memory`
fallback preserves one process's availability but is neither shared nor
restart-safe, so multi-worker production deployments should retain `deny` and
alert on `shared_backend_unavailable`. Without `--rate-limit-store`, the local
memory backend is intentional and `fura mcp --describe --json` reports
`shared: false` and `restart_safe: false`.

## HTTP and static sidecars

`json` means a JSON object, `text` means UTF-8 text, `xml` is sitemap XML,
`binary` is Sphinx inventory bytes, and `event-stream` is SSE. Query routes are
live HTTP contracts; exportable routes are copied into `app/public` with the
configured base path.

| URL | Media/shape | Stable schema or purpose |
|---|---|---|
| `/catalog.json` | `json` | Catalog/DCP graph with schema version, pages, edges, and graph nodes. |
| `/catalog/api-operations.json` | `json` | `schema_version`, operation count, and API `operations`. |
| `/catalog/artifacts.json` | `json` | Freeze/export presence, manifest validity, generation time, age, upstream freshness, counts, and paths. |
| `/catalog/freshness.json` | `json` | Source, index, freeze, and export freshness signals plus remediation. |
| `/catalog/operational-status.json` | `json` | Combined health, readiness, freshness, and artifact contracts from one observation. |
| `/catalog/query.json` | `json` | Query echo plus `page_count`, `edge_count`, `pages`, `edges`, and `graph_nodes`. |
| `/catalog/retrieve` | `json` | Retrieved node, chunks, backlinks, related context, and API operation metadata. |
| `/catalog/source-health.json` | `json` | `ok`, `mount_count`, `active_channel`, `serve_mode`, and `mounts`. |
| `/channels.json` | `json` | Active/default channel and per-mount channel manifests. |
| `/deployment-profiles.json` | `json` | `default_profile`, `profiles`, `agent_modes`, and `links`. |
| `/docs/_author/events` | `event-stream` | Author invalidation events; live author mode only. |
| `/docs/_author/stale` | `json` | Stale source/output entries and impact summary; author mode only. |
| `/graph/query.json` | `json` | Compatibility alias for the catalog graph query schema. |
| `/healthz` | `json` | Process liveness only; HTTP 200 while the process can answer, independent of source/artifact state. |
| `/index.txt` | `text` | Per-page machine-readable text index with title, URL, and body. |
| `/inventories.json` | `json` | Inventory manifest with ids, URLs, domains, and versions. |
| `/inventories/{inventory_id}/objects.inv` | `binary` | One named Sphinx v2 inventory. Unknown ids return not found. |
| `/llms-full.txt` | `text` | Full public corpus projection for LLM consumers. |
| `/llms.txt` | `text` | Compact public page/API index and descriptions. |
| `/meta.json` | `json` | Public site, channel, catalog, and agent-output metadata. |
| `/objects.inv` | `binary` | Default Sphinx v2 reference inventory. |
| `/readyz` | `json` | Safe-to-serve checks; HTTP 200 for `ready`, HTTP 503 for `not_ready`. |
| `/routes.json` | `json` | `route_count` and route records with methods, path, handler, response, template, and fragment contracts. |
| `/search.json` | `json` | Public search records with URLs, text, tags, chunks, API hints, and provenance. |
| `/search/semantic` | `json` | Query, hybrid ranking mode, count, and accessible result records. |
| `/semantic.json` | `json` | Frozen semantic chunks and embedding/search metadata. |
| `/sitemap.xml` | `xml` | Public canonical URLs only. |
| `/structure.json` | `json` | Content-IR heading/directive structure keyed by public node. |
| `/surface.json` | `json` | Product-surface manifest and linked machine-readable URLs. |
| `/tools.json` | `json` | Agent tool descriptors, schemas, and API-operation discovery metadata. |

## Health, readiness, freshness, and artifact age

These signals are intentionally distinct. Do not use `/healthz` as a traffic
readiness probe: it proves only that the process can answer. Use `/readyz` for
load-balancer admission and rollout gates.

| Contract | HTTP | Stable fields | Meaning |
|---|---:|---|---|
| `/healthz` | 200 | `kind=health`, `ok`, `status`, `observed_at`, `process.pid`, `process.responsive` | Process liveness; no source, index, freeze, or export checks. |
| `/readyz` ready | 200 | `kind=readiness`, `ok=true`, `status=ready`, `checks`, `remediation=[]` | Every mount source and index passes; preview/hybrid also has a valid current freeze. |
| `/readyz` not ready | 503 | `kind=readiness`, `ok=false`, `status=not_ready`, failed `checks`, `remediation` | Do not admit traffic; perform the named source, index, or freeze repair. |
| `/catalog/freshness.json` | 200 | `status`, `signals`, `remediation` | Reports `fresh`, `stale`, `degraded`, or `unknown` without treating staleness as process death. |
| `/catalog/artifacts.json` | 200 | `freeze`, `export`; each has `exists`, `valid`, `required`, `freshness`, `generated_at`, `age_seconds`, counts, and paths | Artifact inventory and age; freeze compares with source mtimes, export compares with freeze. |

All payloads use `schema_version: 1`, include `kind`, `http_status`, and an UTC
`observed_at`, and are combined at `/catalog/operational-status.json`. A failed
source sync instructs operators to restore source access; a failed index directs
an index or frozen-shard rebuild; stale freeze/export signals direct `fura freeze`
or `fura export --fresh`. Author mode does not require deployment artifacts for
readiness. Preview and hybrid modes require a valid non-stale freeze because it
is part of their safe serving path.

## Diagnostics

Diagnostics use `severity`, `message`, optional `source_path`/`line`, stable
`rule_id`, and actionable `next_action`. CLI validation errors exit 2,
configuration errors exit 3, and source conflicts exit 4. Pattern ids ending in
`*` are families whose concrete suffix carries the upstream category or eval id.

| Family | Meaning and first response |
|---|---|
| `chirp.*`, `chirp.templating` | Chirp route/template/design-system contracts; fix the named upstream contract. |
| `fura.agent.*`, `fura.agent_safety.*` | MCP/schema/description/parity or public-context safety; run `fura check --agent --json`. |
| `fura.author.*`, `fura.lifecycle` | Authorization, CSRF, method, target, revision, or lifecycle failure; inspect diagnostics and rerun a dry run. |
| `fura.mcp*`, `fura.evals.*` | MCP protocol/policy/rate/token or deterministic eval failure; repair policy/schema before retry. |
| `fura.migration.*`, `fura.migrate*` | Source-format compatibility or incomplete migration; use the migration report and suggested mapping. |
| `fura.content`, `fura.api`, `fura.dcp`, `fura.check` | Content, OpenAPI, graph-schema, or aggregate validation; fix the cited source. |
| `fura.impact.stale_public_output`, `fura.visibility_leak` | Public artifact is stale or exposes protected content; rebuild or block promotion. |
| `fura.identity.*` | Trusted gateway claims are missing, ambiguous, spoofable, or conflict with tenant/site identity; reject the request and repair the deployment-owned claim mapping. |
| `fura.docs_quality.*`, `fura.docs_quality.exemption` | Documentation completeness or stale exemption; follow the named owner and page-type recommendation. |
| `fura.scorecard.*` | Adoption gate is unmet; route the documented remediation to the gate owner before the next decision date. |

Exact rule-id index:

- `chirp.*`, `chirp.templating`
- `fura.agent.action_boundary`, `fura.agent.breaking_change`, `fura.agent.contract_diff`, `fura.agent.description`, `fura.agent.duplicate`
- `fura.agent.input_schema`, `fura.agent.llms_description`, `fura.agent.manifest_alignment`, `fura.agent.milo`, `fura.agent.mutation_boundary`
- `fura.agent.output_schema`, `fura.agent.parameter_description`, `fura.agent.permission_note`, `fura.agent.resource_metadata`, `fura.agent.tool_description`
- `fura.agent_safety.private_leak`, `fura.agent_safety.stale_context`
- `fura.api`, `fura.author`, `fura.author.authorization`, `fura.author.conflict`, `fura.author.csrf`, `fura.author.method`, `fura.author.target`
- `fura.check`, `fura.content`, `fura.dcp`, `fura.docs_reference.drift`, `fura.evals.*`, `fura.evals.retrieval_regression`
- `fura.docs_quality.*`, `fura.docs_quality.exemption`
- `fura.impact.stale_public_output`, `fura.lifecycle`, `fura.mcp`, `fura.mcp.author`, `fura.mcp.privileged_token`, `fura.mcp.rate_limit`
- `fura.identity.*`
- `fura.migrate`, `fura.migrate.unmigrated_component`, `fura.migration.compat.mdx`, `fura.migration.compat.myst`, `fura.migration.compat.rst`, `fura.migration.remediation.manual`, `fura.migration.report`
- `fura.pdf`, `fura.recipes`, `fura.visibility_leak`
- `fura.scorecard.*`
- `fura.catalog`, `fura.config`, `fura.source_sync`, `fura.content_parse`, `fura.access`, `fura.access_denied`, `fura.catalog_load`, `fura.export`

## Lifecycle, roles, teams, and export filtering

Roles are ordered and inherit lower capabilities.

| Role | Baseline capability |
|---|---|
| `anonymous` | `read`, `search`, `retrieve`, and `export` public content. |
| `reader` | Anonymous capabilities plus unlisted, internal, and private reads. |
| `contributor` | Reader plus `author` and draft reads. |
| `publisher` | Contributor plus `publish` and `unpublish`. |
| `admin` | All capabilities, including `configure`, `administer`, and archive. |

| Visibility | Minimum read/search/retrieve/export role | Public anonymous export |
|---|---|---|
| `public` | `anonymous` | yes, unless roles, teams, or admin-only policy restricts it |
| `unlisted` | `reader` | no |
| `internal` | `reader` | no |
| `private` | `reader` | no |
| `draft` | `contributor` | no |
| `archived` | `admin` | no |

Mount policy is checked before page policy. A `roles` list requires one named
role or a stronger role. A `teams` list requires membership in at least one
named team; only `admin` bypasses the team requirement. `admin_only: true`
requires admin regardless of lifecycle state. Request arguments cannot choose
their effective actor, roles, or teams.

Anonymous export permission is applied consistently to browser/static routes,
`/catalog.json`, `/catalog/api-operations.json`, `/search.json`, `/tools.json`,
`/meta.json`, `/structure.json`, `/llms.txt`, `/llms-full.txt`, `/sitemap.xml`,
`/index.txt`, MCP resources, retrieval, graph traversal, and semantic search.
`include_private=true` is an author inspection capability, never a public export
setting. GitHub Pages and unauthenticated sessions always fail closed.

## Deployment profiles

| Profile id | Services and storage | Auth and publishing | Operational failure boundary |
|---|---|---|---|
| `local-author` | Python, filesystem, browser; checkout plus optional `app/frozen` | Local-process trust; freeze/export only when publishing | Not public; exposing author routes off-loopback is unsupported without deployment identity. |
| `static-pages` | Python build runner and static host; `app/frozen` + `app/public` | Anonymous read-only artifact with protected content filtered | Build, base-path, or stale-artifact failure blocks promotion; no live authoring or privileged MCP. |
| `cloud-live` | Python service, reverse proxy, git/filesystem mount, optional frozen cache | Platform identity before author routes; live routes plus optional artifacts | Service health, identity, and source-sync failures require operator remediation. |
| `self-hosted-enterprise` | Python service, IdP, source sync, observability, tenant-scoped stores | Gateway/SSO and privileged author MCP; reviewed live/static/PDF/agent channels | Identity, audit, tenant/site isolation, sync health, and rollout policy are operator-owned. |

The machine contract is `/deployment-profiles.json`; `default_profile` is
`local-author`. Consumers should select by profile `id`, not display label.

## Source-provider health and failure states

`/catalog/source-health.json`, `fura://catalog/sources`, and
`inspect_source_health` expose the same top-level contract. Each mount includes
`status`, `provider`, `content_root`, `exists`, `loaded`, `loaded_from`, tracked
extensions, file/page counts, channels, and nested `source` and `index` objects.
Both nested objects expose `status`, `stage`, and a sanitized `error` record on
failure. Operators should alert on `source.status`, group sync failures by
`source.stage`, alert separately on `index.status`, and group index failures by
`index.stage`.

| Observed state | Meaning | Required response |
|---|---|---|
| `healthy` | Source sync succeeded (or filesystem source is available) and a shard loaded. | No action. Monitor resolved ref and page count. |
| `degraded` | A shard loaded, but `source.status` or `index.status` is `failed`, or either contains `error`. | Keep the last usable catalog only for inspection; repair and reindex before publishing. |
| `unavailable` | No shard loaded, regardless of sync result. | Treat the mount as offline and block dependent publication/retrieval. |
| Filesystem root missing or unreadable | `exists=false`, failed/empty index, or scan/read exception. | Correct `content_root` and permissions, then rerun `fura check` or restart author mode. |
| Git executable missing | Sync error says `git executable is required for git-backed mounts`. | Install git in the runtime image and retry. |
| Git clone/fetch/checkout/rev-parse failed | `source.status=failed`, `source.stage=sync`, with the sanitized command failure. | Verify repository URL, credentials/network, requested ref, and checkout permissions. |
| Git subpath absent | Sync error says the configured source path does not exist. | Correct `source.path`/`sparse_path` or publish that directory at the selected ref. |
| Parse/front matter/adapter failure | `index.status=failed` at the recorded stage. | Fix the cited source or install the required format adapter, then reindex. |
| Frozen shard incompatible/corrupt | Index stage is `frozen_load`; live fallback may produce `degraded`, otherwise `unavailable`. | Rebuild `app/frozen` with the current release and redeploy atomically. |

Do not infer health from HTTP 200 alone: require top-level `ok=true` and every
required mount to be `healthy`. A degraded cached shard is intentionally visible
for diagnosis but is not proof that current source was indexed.
