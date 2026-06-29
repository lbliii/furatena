# Agent Workflow Recipes

Furatena exposes stable command recipes through `fura recipes`. Agents should read these recipes instead of inferring hidden state from terminal output.

```bash
fura recipes
fura recipes validate --json
```

Each recipe is safe to consume as JSON through the standard CLI envelope documented in [CLI_CONTRACT.md](CLI_CONTRACT.md).

## Recipes

- `init` — scaffold a new app, validate it, and start author preview.
- `inspect` — inspect an existing catalog, theme resolution, and graph sample before editing.
- `validate` — run strict content, view, API-surface, and deploy checks for CI or approval gates.
- `query` — retrieve graph nodes by heading, directive, namespace, tag, edition, or URL prefix.
- `publish` — freeze catalog IR, export static HTML, and preview the frozen build before deployment.
- `repair` — collect diagnostics, preview risky rewrites, and require approval before mutating sources.
- `author-draft` — create and inspect a draft page with dry-run preview first.
- `author-edit-publish` — read, edit, validate, and publish through reviewable MCP tool results.
- `author-stale-repair` — inspect stale impact, preview a source fix, and validate after repair.
- `author-publish-remediation` — recover from failed publish attempts with diagnostics-first repairs.
- `source-sync` — refresh git-backed sources, validate them, and rebuild frozen graph outputs.

## Agent Surfaces

Codex, Claude Code, and Cursor should prefer:

```bash
fura recipes <id> --json
```

CI should run the strict validation and publishing recipes:

```bash
fura check --deploy --agent --warnings-as-errors --json
fura evals --json
fura recipes publish --json
```

Local shell users can run `fura recipes <id>` for readable commands, then copy the relevant sequence and replace placeholders such as `<APP_ROOT>`, `<BASE_URL>`, and `<TEXT>`.

## MCP Server

Agents that support MCP can connect to the local catalog server over stdio:

```bash
fura mcp --author
fura mcp --author --include-private
fura mcp --preview --frozen-dir app/frozen
fura mcp --remote --tenant acme --site docs --privileged-token <TOKEN>
fura mcp --describe --json
```

The server exposes catalog nodes, API/autodoc operations, structure indexes, inventories, source health, channel manifests, validation reports, and stale-impact reports as JSON resources.
Draft, private, internal, unlisted, and archived pages are hidden by default; `--include-private` is an explicit author-mode opt-in.
The stdio transport is served through Milo's MCP runtime, while Furatena owns the catalog-specific resource and tool definitions.
Remote MCP sessions should start with `--remote` and stable `--actor`, `--tenant`, and `--site` metadata. Remote sessions deny sensitive authoring tools unless the request includes a valid `privileged_token`; remote `--include-private` only enables private content when a privileged token is configured.
The `fura://reports/audit` resource records sanitized tool calls with actor, tenant, site, tool name, redacted inputs, result status, duration, and the configured timeout. `--rate-limit`, `--timeout`, and `--max-output-chars` define per-session call limits, timeout metadata, and output truncation bounds for local and remote transports.
Run `fura check --agent --json` before publishing MCP changes; it lints tool/resource descriptions, input/output schemas, mutating-tool permission boundaries, Milo adapter parity, and descriptions that feed llms/search exports.
Run `fura evals --json` for deterministic golden-path agent checks. The suite exercises the Milo MCP adapter for prose retrieval, API operation discovery, private-content boundaries, version/channel metadata, stale-impact reports, multi-mount hubs, tool selection, and non-mutating author workflows without paid model calls.

Run `fura evals --include-private --category author_workflows --json` to verify author drafting, publish preview, and failed-publish remediation paths. These evals use dry-run or intentionally unconfirmed writes so source files remain unchanged.

MCP tools return both text content and `structuredContent` payloads:

- `semantic_search` — hybrid keyword and semantic search over pages and chunks.
- `retrieve_node` — node metadata, chunks, backlinks, and similar chunks.
- `traverse_graph` — backlinks, child pages, outbound links, and neighboring pages.
- `inspect_source_health` — mount roots, tracked extensions, page counts, and channels.
- `run_checks` — structured validation errors and warnings.
- `explain_stale_impact` — stale entries and refresh targets for author workflows.
- `author_create_draft` — create a draft source file; dry-run by default.
- `author_read_source` — read source only from an include-private author MCP session.
- `author_propose_edit` — preview an exact-text source edit without writing files.
- `author_apply_edit` — apply an exact-text source edit after explicit confirmation.
- `author_validate` — run validation, optionally scoped to one author target.
- `author_publish` / `author_unpublish` — change lifecycle state; dry-run by default.
- `author_inspect_publication_impact` — return lifecycle status, validation, and stale impact before a publication change.

Authoring MCP tools require `fura mcp --author --include-private`. Mutating tools default to dry-run behavior and return `isError: true` if a write is requested without `confirmed: true`.

## Author Lifecycle

For local source mutations, agents should use `fura author` with `--dry-run --json` before writing and `--yes --json` only after confirmation:

```bash
fura author new docs/proposed-page --title "Proposed page" --dry-run --json
fura author edit docs/proposed-page --old-text "Draft" --new-text "Reviewed draft" --dry-run --json
fura author edit docs/proposed-page --old-text "Draft" --new-text "Reviewed draft" --yes --json
fura author publish docs/proposed-page --dry-run --json
fura author publish docs/proposed-page --yes --json
```

Lifecycle responses include operation id, target path, mount id, previous/resulting visibility, changed files, diagnostics, diff preview, and next actions. Transition responses also include `publication_impact` so agents can see whether navigation, search, export, and public retrieval/LLM surfaces are affected before writing. `fura author edit` uses exact source span replacement, so run `fura author status` or `author_read_source` first and pass the exact `--old-text` value to avoid stale edits.

## Approval Behavior

Recipe steps include `dry_run` and `requires_confirmation` flags in JSON. Agents should run dry-run steps before writes and pause for explicit approval before any step marked `requires_confirmation`.
