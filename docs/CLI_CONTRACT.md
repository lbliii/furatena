# Fura CLI Automation Contract

The CLI is composed from one module per top-level command under
`furatena.cli.commands`. Each module owns its arguments, help text, runner, and
result shaping, exposed through the immutable `CommandModule` registration
contract. `cli/main.py` contains only global parser options, command
registration, parsing, and dispatch.

Python callers and command tests can bypass presentation with
`furatena.cli.main.run_command(argv)`. It returns the command's structured
`CommandResult` directly and does not write terminal or JSON output. The
console entry point applies presentation and exit-code behavior afterward.
This keeps command logic fast and deterministic in-process while a small
subprocess smoke suite verifies the installed `fura` entry point itself.

Fura commands that support `--json` emit one stable JSON object:

```json
{
  "ok": true,
  "command": "check",
  "exit_code": 0,
  "summary": "check completed with 0 error(s), 0 warning(s), and 0 info finding(s)",
  "diagnostics": [],
  "data": {}
}
```

## Exit codes

- `0` success
- `1` warning-level automation failure, such as no query matches or warnings promoted to failure
- `2` validation error in content, migrated sources, catalog data, or app contracts
- `3` configuration error
- `4` source or sync failure
- `70` internal error

## Diagnostics

Diagnostics are structured for CI and agents:

```json
{
  "severity": "error",
  "message": "unknown directive: beta",
  "source_path": "content/docs/page.md",
  "line": 12,
  "rule_id": "fura.content",
  "next_action": "Fix the content validation error and rerun fura check."
}
```

Fields such as `source_path`, `line`, `mount`, `node_id`, `rule_id`, and `next_action` are present when Fura can determine them. Commands must not require screen scraping of human text when `--json` is used.

Normal `fura check` runs compose Chirp's structured hypermedia findings into
this same diagnostic array. Chirp categories use stable `chirp.<category>` rule
ids, template or route origins populate `source_path`, and Chirp details (or a
category-specific fallback) populate `next_action`. `data` reports total and
Chirp-specific error, warning, and info counts plus routes/templates checked.
Info findings never fail the command; warnings fail when the configured
warning threshold applies, including `--warnings-as-errors` and Chirp's deploy
posture. Terminal, JSON, and CI report formats render this one composed result,
so no finding is counted or printed twice.

`tests/fixtures/diagnostics.json` is the versioned golden dataset for this
contract. Its clean, warning-only, and error cases preserve each finding's id,
severity, message, origin, and remediation, then verify matching summaries and
exit codes in both terminal and JSON modes.

## Current JSON commands

- `fura init --json`
- `fura serve --json`
- `fura stop --json`
- `fura check --json`
- `fura check --agent --json`
- `fura docs-inventory --json`
- `fura docs-reference --output PATH --json [--check]`
- `fura api-diff OLD.yaml NEW.yaml --json`
- `fura agent-diff OLD.json NEW.json --json [--decision TEXT]`
- `fura query --json`
- `fura freeze --json`
- `fura export --json`
- `fura pdf --json`
- `fura promotion current|history|status --state-root PATH --json`
- `fura migrate --json`
- `fura activation start|mark|report --json`
- `fura scorecard --json`
- `fura recipes --json`
- `fura evals --json`
- `fura mcp --describe --json`
- `fura author new|status|validate|edit|draft|publish|unpublish|archive --json`
- `fura theme list --json`
- `fura theme inspect --json`
- `fura theme eject --json`
- `fura theme diff --json`
- `fura theme init --json`

`fura migrate --report --json` is read-only and emits `data.migration_report` with `summary`, `groups`, `findings`, and a `remediation_plan`. The groups cover severity, source path, owner, construct, and next action; playbook groups add ecosystem and risk. Findings compose `fura check` diagnostics with format compatibility findings for embedded MDX JSX, RST directives/roles, and MyST directives/roles.

`fura migrate --apply-safe [PATH ...] --json` creates canonical `.md` siblings only for deterministic, parse-clean MDX conversions with no unmapped JSX and no conflicting target. Sources are never removed and existing targets are never overwritten. `--dry-run` previews the same decisions without writing. Manual items emit `fura.migration.remediation.manual` and warning exit code `1`.

`fura init --starter minimal|api-portal|multi-mount` creates a maintained standalone repository profile. Every profile includes an exact dependency on the generating Furatena release, CPython `3.14` compatibility, a `3.14t`/`PYTHON_GIL=0` GitHub workflow, documented audience and first edit, and clone-to-check-to-freeze-to-export commands. The API portal adds a lint-clean OpenAPI projection; the multi-mount profile adds independently rooted product, SDK, and operations mounts.

`fura docs-inventory --json` derives public CLI commands, routes, config fields,
MCP tools/resources, sidecars, diagnostic rule ids, and deployment profiles from
runtime metadata. Records link stable identifiers to matching documentation and
include implementation/documentation fingerprints. A prior `--baseline`, or an
existing `--output` file, marks implementation changes with unchanged docs as
stale; missing and stale ids are emitted as explicit machine-readable lists.

`fura docs-reference --output PATH` renders every active command and parser option,
docs.yaml/mounts.yaml field and default, plus source-observed FURA_/CHIRP_
environment controls. `--check` compares the generated bytes with the committed
page and returns validation exit code `2` with `fura.docs_reference.drift` and a
regeneration action when they differ.

`fura promotion current|history|status` reads the versioned provider-neutral
promotion store. `current` and `history` require a deployed `--environment`;
`status` requires the immutable `--operation-id`. All three require an explicit
private `--state-root`, use the standard JSON envelope, and remove actor and
idempotency identity. There are intentionally no CLI promotion or rollback mutation
commands: only trusted deployment automation receives deploy authority.

`fura mcp` without `--describe` runs an MCP stdio server on Milo's MCP runtime. Its `tools/call` responses include `structuredContent` alongside text content so agents do not need to parse prose. `fura mcp --describe --json` includes `policy`, `audit`, `rate_limit`, `resources`, and `tools`; the resources include `fura://reports/audit` for sanitized tool-call audit events. `--audit-store PATH` enables restart-safe JSONL persistence and `--audit-retention-days` bounds retained history; without a path, audit events remain in a thread-safe process-local store. Both backends record event/correlation identity, actor, tenant, action, target, and outcome, redact secrets plus authored/query content before storage, and support filtered reads and JSON export through the provider-neutral audit-store interface. `--rate-limit-store PATH` enables transactional shared SQLite counters that survive restarts and serialize workers. The policy combines per-actor burst and sustained limits, an aggregate tenant limit, and a lower sensitive-tool limit. Shared-backend errors fail closed by default; `--rate-limit-fallback memory` is an explicit availability tradeoff that is not safe against multi-worker bypass. API operation resources include a `try_it` contract that separates static render-only, local mock/sample, and authenticated live-proxy behavior while keeping token references server-only. Public exports mirror that structure through `/catalog/api-operations.json`, API-aware `search.json` entries, API hints in `llms.txt`, and `tools.json` metadata for `list_api_operations`. Authoring MCP tools require `--author --include-private`, default mutating operations to dry-run, and include audit metadata for actor, command, target path, state transition, diagnostics, dry-run state, and confirmation state. Author lifecycle transition responses include `publication_impact` with affected navigation, search, export, and agent surfaces. Stale-impact responses include owner, source, mount, tenant, workspace, site, and output-channel groupings so reports can route repair work without scraping page records. Remote sessions should use `--remote` with actor/tenant/site metadata, rate/output bounds, durable audit and rate-limit paths, and a `--privileged-token` before sensitive authoring tools or private content are exposed.

`fura check --agent --json` extends normal validation with agent-facing contract lint for MCP resources, MCP tool schemas, Milo adapter parity, llms/search descriptions, and agent-safety checks for stale or private context. `fura check --agent-only --json` runs just the fast MCP/resource contract gate for local surface checks. Normal content checks also lint OpenAPI specs referenced by autodoc config for invalid specs, missing operation metadata, broken examples, unresolved schema references, rendering-head contract warnings for unsupported directives or missing head fields, and delivery config errors for unknown heads or theme packs.

`fura check --report-format github|junit|checkstyle|markdown` renders the same diagnostics as GitHub Actions annotations, JUnit XML, checkstyle XML, or a markdown summary. The command keeps the same exit-code behavior as normal checks, so CI can fail on errors or on warnings when `--warnings-as-errors` is set while still surfacing warnings in review tools.

`fura api-diff OLD.yaml NEW.yaml --json` compares two OpenAPI specs by operation and reports added, removed, changed, and breaking operation summaries. Terminal output is readable for release notes; JSON output preserves the same counts and per-operation change reasons for CI or changelog automation.

`fura agent-diff OLD.json NEW.json --json` compares versioned agent-output fixtures by stable record identity instead of byte order. It reports added, removed, changed, and breaking paths across sidecars and MCP contracts. Breaking removals, type/version changes, and URL or URI changes return validation exit code `2` until `--decision` records the explicit compatibility or migration decision.

`fura pdf --json` exports one public page (`--page`), one public collection (`--collection`), or the full public site as PDF artifacts. The JSON `data` includes `output_dir`, `target`, generated `paths`, physical-sheet `page_count`, selected-catalog `node_count`, `byte_count`, and whether `channels.json` was refreshed. `--paper letter|a4` selects the page box and `--grayscale` uses the print-safe grayscale palette. By default artifacts are written under `app/public/pdf/` and the public channel manifest is updated with available PDF outputs. Consumers that previously treated `page_count` as selected nodes must migrate to `node_count`.

`fura impact --json` emits a CI-friendly stale-content impact report without requiring an MCP session. The payload includes stale entries, affected chunks, graph context, changed graph edges touching each DCP node, provenance, owner/source/channel groupings, recommended remediation, and GitHub-issue-ready `repair_tasks` plus `task_markdown`. It combines live author invalidations with frozen public-output freshness checks so local, static, and deployed workflows can route repair work from the same structured contract.

Operational HTTP contracts separate process liveness from serving safety. `/healthz` returns HTTP 200 whenever the process can answer and does not inspect catalogs or artifacts. `/readyz` returns 200 only when mount source/index checks and any preview/hybrid freeze requirement pass; otherwise it returns 503 with failed checks and remediation. `/catalog/freshness.json` reports source-to-freeze-to-export drift, `/catalog/artifacts.json` reports manifests, paths, generation timestamps and ages, and `/catalog/operational-status.json` combines all four versioned contracts.

`FURA_STRUCTURED_LOGS=1` emits privacy-safe JSON operational events with stable name, event/correlation identity, UTC timestamp, severity, status, and attributes. `FURA_TELEMETRY=opentelemetry` optionally maps the same envelope to spans and the `furatena.operational.events` counter when the OpenTelemetry API plus an operator-configured SDK/exporter are installed; `none` is the default. Telemetry never changes the canonical event names or redaction boundary.

Sync, freeze, and export use renewable cross-process filesystem leases. `FURA_OPERATION_LOCK_TIMEOUT` (30 seconds) bounds acquisition and reports the current owner on timeout; `FURA_OPERATION_LEASE_SECONDS` (3600 seconds) bounds crashed-worker recovery while active workers heartbeat. Freeze/export build in sibling pending trees and atomically promote completed output. Restart reconciliation restores orphaned backups, removes partial pending trees, and preserves the last complete deployment manifest.

`fura activation` implements the opt-in activation measurement protocol. `start` requires explicit `--consent` and records only a local monotonic origin, a random local session id, and the `new-site` or `imported-site` journey. `mark` records elapsed duration for first edit, first publish, or clean migration plus separately supplied automated and manual remediation seconds. `report` removes session ids, origins, and file paths, never transmits data, and aggregates the two journey types separately against the documented first-edit targets.

`fura scorecard --input EVIDENCE.json --output SCORECARD.json` evaluates the versioned beta adoption-readiness policy. The strict input manifest records a decision date, all six evidence areas, and an owner plus remediation for every area. The report includes the policy version, canonical input SHA-256, all fixed gates, and a `go` or `no-go` decision. Missing measurements are explicit `null` values and fail their gate; no target is inferred from the input. A no-go result uses exit code `2` and emits one structured diagnostic per unmet gate.

Recoverable domain failures use `CatalogError` subclasses and the same JSON
diagnostic envelope. The base code is `fura.catalog`; specialized codes are
`fura.config`, `fura.source_sync`,
`fura.content_parse`, `fura.access`, `fura.access_denied`, `fura.catalog_load`,
and `fura.export`. `data.error.context` carries available `path`, `mount`, `slug`,
and `operation` fields. Compatibility is preserved: configuration/content/access
policy errors remain `ValueError` subclasses, missing frozen catalogs remain a
`FileNotFoundError`, denied access remains a `PermissionError`, and source/export
failures remain `RuntimeError` subclasses.

`fura evals --json` runs deterministic, fixture-style agent evaluations through the Milo MCP adapter. The lightweight suite covers prose retrieval, API operation discovery, private-content boundaries, version/channel metadata, stale-impact reports, multi-mount hubs, tool selection, and author workflows without paid model calls. Use `--include-private --category author_workflows` to exercise draft dry-run, publish preview, validation-error repair, failed-publish remediation, and a reversible publish/unpublish retrieval-boundary check.

See [AGENT_WORKFLOWS.md](AGENT_WORKFLOWS.md) for stable command sequences over these JSON-capable commands.
