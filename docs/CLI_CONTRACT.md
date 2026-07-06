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
- `fura api-diff OLD.yaml NEW.yaml --json`
- `fura query --json`
- `fura freeze --json`
- `fura export --json`
- `fura pdf --json`
- `fura migrate --json`
- `fura recipes --json`
- `fura evals --json`
- `fura mcp --describe --json`
- `fura author new|status|validate|edit|draft|publish|unpublish|archive --json`
- `fura theme list --json`
- `fura theme inspect --json`
- `fura theme eject --json`
- `fura theme diff --json`
- `fura theme init --json`

`fura migrate --report --json` is read-only and emits `data.migration_report` with `summary`, `groups`, and `findings`. The groups cover severity, source path, construct, and next action; findings compose `fura check` diagnostics with format compatibility findings for embedded MDX JSX, RST directives/roles, and MyST directives/roles.

`fura mcp` without `--describe` runs an MCP stdio server on Milo's MCP runtime. Its `tools/call` responses include `structuredContent` alongside text content so agents do not need to parse prose. `fura mcp --describe --json` includes `policy`, `resources`, and `tools`; the resources include `fura://reports/audit` for sanitized tool-call audit events. API operation resources include a `try_it` contract that separates static render-only, local mock/sample, and authenticated live-proxy behavior while keeping token references server-only. Public exports mirror that structure through `/catalog/api-operations.json`, API-aware `search.json` entries, API hints in `llms.txt`, and `tools.json` metadata for `list_api_operations`. Authoring MCP tools require `--author --include-private`, default mutating operations to dry-run, and include audit metadata for actor, command, target path, state transition, diagnostics, dry-run state, and confirmation state. Author lifecycle transition responses include `publication_impact` with affected navigation, search, export, and agent surfaces. Stale-impact responses include owner, source, mount, tenant, workspace, site, and output-channel groupings so reports can route repair work without scraping page records. Remote sessions should use `--remote` with actor/tenant/site metadata, rate/output bounds, and a `--privileged-token` before sensitive authoring tools or private content are exposed.

`fura check --agent --json` extends normal validation with agent-facing contract lint for MCP resources, MCP tool schemas, Milo adapter parity, llms/search descriptions, and agent-safety checks for stale or private context. `fura check --agent-only --json` runs just the fast MCP/resource contract gate for local surface checks. Normal content checks also lint OpenAPI specs referenced by autodoc config for invalid specs, missing operation metadata, broken examples, unresolved schema references, rendering-head contract warnings for unsupported directives or missing head fields, and delivery config errors for unknown heads or theme packs.

`fura check --report-format github|junit|checkstyle|markdown` renders the same diagnostics as GitHub Actions annotations, JUnit XML, checkstyle XML, or a markdown summary. The command keeps the same exit-code behavior as normal checks, so CI can fail on errors or on warnings when `--warnings-as-errors` is set while still surfacing warnings in review tools.

`fura api-diff OLD.yaml NEW.yaml --json` compares two OpenAPI specs by operation and reports added, removed, changed, and breaking operation summaries. Terminal output is readable for release notes; JSON output preserves the same counts and per-operation change reasons for CI or changelog automation.

`fura pdf --json` exports one public page (`--page`), one public collection (`--collection`), or the full public site as PDF artifacts. The JSON `data` includes `output_dir`, `target`, generated `paths`, `page_count`, `byte_count`, and whether `channels.json` was refreshed. By default artifacts are written under `app/public/pdf/` and the public channel manifest is updated with available PDF outputs.

`fura impact --json` emits a CI-friendly stale-content impact report without requiring an MCP session. The payload includes stale entries, affected chunks, graph context, changed graph edges touching each DCP node, provenance, owner/source/channel groupings, recommended remediation, and GitHub-issue-ready `repair_tasks` plus `task_markdown`. It combines live author invalidations with frozen public-output freshness checks so local, static, and deployed workflows can route repair work from the same structured contract.

`fura evals --json` runs deterministic, fixture-style agent evaluations through the Milo MCP adapter. The lightweight suite covers prose retrieval, API operation discovery, private-content boundaries, version/channel metadata, stale-impact reports, multi-mount hubs, tool selection, and author workflows without paid model calls. Use `--include-private --category author_workflows` to exercise draft dry-run, publish preview, validation-error repair, failed-publish remediation, and a reversible publish/unpublish retrieval-boundary check.

See [AGENT_WORKFLOWS.md](AGENT_WORKFLOWS.md) for stable command sequences over these JSON-capable commands.
