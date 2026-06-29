# Fura CLI Automation Contract

Fura commands that support `--json` emit one stable JSON object:

```json
{
  "ok": true,
  "command": "check",
  "exit_code": 0,
  "summary": "check completed with 0 error(s) and 0 warning(s)",
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

## Current JSON commands

- `fura init --json`
- `fura serve --json`
- `fura stop --json`
- `fura check --json`
- `fura check --agent --json`
- `fura query --json`
- `fura freeze --json`
- `fura export --json`
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

`fura mcp` without `--describe` runs an MCP stdio server on Milo's MCP runtime. Its `tools/call` responses include `structuredContent` alongside text content so agents do not need to parse prose. `fura mcp --describe --json` includes `policy`, `resources`, and `tools`; the resources include `fura://reports/audit` for sanitized tool-call audit events. Authoring MCP tools require `--author --include-private`, default mutating operations to dry-run, and include audit metadata for actor, command, target path, state transition, diagnostics, dry-run state, and confirmation state. Author lifecycle transition responses include `publication_impact` with affected navigation, search, export, and agent surfaces. Stale-impact responses include owner, source, mount, and channel groupings so reports can route repair work without scraping page records. Remote sessions should use `--remote` with actor/tenant/site metadata, rate/output bounds, and a `--privileged-token` before sensitive authoring tools or private content are exposed.

`fura check --agent --json` extends normal validation with agent-facing contract lint for MCP resources, MCP tool schemas, Milo adapter parity, and llms/search descriptions. `fura check --agent-only --json` runs just that gate for fast CI or local MCP surface checks.

`fura evals --json` runs deterministic, fixture-style agent evaluations through the Milo MCP adapter. The lightweight suite covers prose retrieval, API operation discovery, private-content boundaries, version/channel metadata, stale-impact reports, multi-mount hubs, tool selection, and author workflows without paid model calls. Use `--include-private --category author_workflows` to exercise draft dry-run, publish preview, failed-publish remediation, and a reversible publish/unpublish retrieval-boundary check.

See [AGENT_WORKFLOWS.md](AGENT_WORKFLOWS.md) for stable command sequences over these JSON-capable commands.
