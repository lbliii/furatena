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
- `query` — retrieve nodes by Content IR filters, DCP graph filters, or MCP `query_graph`.
- `publish` — freeze catalog IR, export static HTML, and preview the frozen build before deployment.
- `repair` — collect diagnostics, preview risky rewrites, and require approval before mutating sources.
- `author-draft` — create and inspect a draft page with dry-run preview first.
- `author-edit-publish` — read, edit, validate, and publish through reviewable MCP tool results.
- `author-stale-repair` — inspect stale impact, preview a source fix, and validate after repair.
- `author-publish-remediation` — recover from failed publish attempts with diagnostics-first repairs.
- `author-archive` — inspect impact, dry-run archive metadata, and remove obsolete pages from public output after approval.
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
For graph traversal, the query recipe includes both HTTP DCP calls such as `/catalog/query.json?edge_kind=<EDGE_KIND>&target=<TARGET>` and the structured MCP `query_graph` tool.

## MCP Server

Agents that support MCP can connect to the local catalog server over stdio:

```bash
fura mcp --author
fura mcp --author --include-private
fura mcp --preview --frozen-dir app/frozen
fura mcp --remote --tenant acme --site docs --privileged-token <TOKEN>
fura mcp --describe --json
```

The server exposes catalog nodes, the DCP catalog graph, API/autodoc operations, structure indexes, inventories, source health, channel manifests, validation reports, and stale-impact reports as JSON resources.
Draft, private, internal, unlisted, and archived pages are hidden by default; `--include-private` is an explicit author-mode opt-in.
The stdio transport is served through Milo's MCP runtime, while Furatena owns the catalog-specific resource and tool definitions.
Remote MCP sessions should start with `--remote` and stable `--actor`, `--tenant`, and `--site` metadata. Remote sessions deny sensitive authoring tools unless the request includes a valid `privileged_token`; remote `--include-private` only enables private content when a privileged token is configured.
The `fura://reports/audit` resource records sanitized tool calls with actor, tenant, site, tool name, redacted inputs, result status, duration, and the configured timeout. Authoring entries also include command, target path, previous/resulting state, diagnostics, dry-run state, and confirmation state for reviewable mutation trails. `--rate-limit`, `--timeout`, and `--max-output-chars` define per-session call limits, timeout metadata, and output truncation bounds for local and remote transports.
Run `fura check --agent --json` before publishing MCP changes; it lints tool/resource descriptions, input/output schemas, mutating-tool permission boundaries, Milo adapter parity, descriptions that feed llms/search exports, stale agent context, and private-content leakage across public agent surfaces.
Use `fura check --report-format github|junit|checkstyle|markdown` when CI or code review tools need annotations, XML reports, or markdown summaries from the same diagnostics.
Run `fura evals --json` for deterministic golden-path agent checks. The suite exercises the Milo MCP adapter for prose retrieval, API operation discovery, private-content boundaries, version/channel metadata, stale-impact reports, multi-mount hubs, tool selection, and non-mutating author workflows without paid model calls.

Versioned public and trusted-author contract fixtures live under `tests/fixtures/agent-contracts/`. Run `fura agent-diff OLD.json NEW.json --json` to review semantic contract changes without treating keyed-array reordering as drift. Breaking removals, type/version changes, and URL or URI changes require an explicit `--decision` describing the major-version or migration policy.

Run `fura evals --include-private --category author_workflows --json` to verify author drafting, publish preview, validation-error repair, failed-publish remediation, and publish/unpublish retrieval boundaries. The suite uses dry-run or intentionally unconfirmed writes for most cases; the validation repair and publish round-trip cases perform confirmed writes against a private fixture and restore the original source before finishing.

## Versioned known-answer corpus

`furatena.catalog.eval_datasets/v1/known_answers.json` is the stable retrieval
quality input. Version 1 records the corpus revision and per-source SHA-256
provenance alongside navigational, factual, troubleshooting, negative, and
access-restricted questions. Expected evidence names node IDs, URLs, heading
anchors, acceptable alternatives, and the browser/DCP/sidecar/MCP surfaces
where each answer must remain consistent.

`fura evals --json` reports the packaged dataset ID, version, corpora, case
count, and query classes. Dataset provenance tests fail when a source changes
without an intentional dataset revision, preventing quality baselines from
silently drifting with the documentation corpus.

The same command executes the fixed cases and reports Recall@3, mean reciprocal
rank (MRR), no-result rate, stale-answer failures, and private leaks overall,
by corpus, and by query class. The packaged `thresholds.json` ratchets the
current CPython free-threaded baseline. An unapproved regression exits with a
validation error. For an intentional transition, pass
`--approve-retrieval-regression "reason"`; the non-empty reason is recorded in
the JSON report. Use `--retrieval-thresholds path/to/policy.json` to test a
proposed policy before changing the packaged ratchet.

`semantic.json` schema version 2 identifies the embedding provider interface,
provider/model versions, deterministic/external behavior, chunk count, and a
stable index fingerprint. `LocalTfidfProvider` remains the dependency-free
default. Integrators can inject `ExternalEmbeddingProvider` with the same build,
query, serialization, and structured failure contracts; Furatena does not
couple that adapter to a hosted service.

MCP tools return both text content and `structuredContent` payloads:

- `semantic_search` — hybrid keyword and semantic search over pages and chunks.
- `retrieve_node` — node metadata, chunks, backlinks, and similar chunks.
- `query_graph` — filtered DCP graph projection by mount, tag, format, owner, locale, edge kind, source, and target.
- `traverse_graph` — backlinks, child pages, outbound links, and neighboring pages.
- `inspect_source_health` — mount roots, tracked extensions, page counts, and channels.
- `run_checks` — structured validation errors and warnings.
- `explain_stale_impact` — stale entries, affected chunks, graph context, refresh targets, repair tasks, and owner/source/mount/tenant/workspace/site/output-channel groupings for author workflows.
- `author_create_draft` — create a draft source file; dry-run by default.
- `author_read_source` — read source only from an include-private author MCP session.
- `author_propose_edit` — preview an exact-text source edit without writing files.
- `author_apply_edit` — apply an exact-text source edit after explicit confirmation.
- `author_validate` — run validation, optionally scoped to one author target.
- `author_publish` / `author_unpublish` / `author_archive` — change lifecycle state; dry-run by default.
- Existing-source writes require the `source_revision` returned by
  `author_read_source`; conflicts include the current revision and require a
  reread/merge/retry cycle.
- `author_inspect_publication_impact` — return lifecycle status, validation, and stale impact before a publication change.

CI and local automation can use `fura impact --json` for the same stale-impact contract without opening MCP. The report includes affected chunks, graph context, changed graph edges touching each DCP node, provenance, output channels, recommended remediation, and GitHub-issue-ready repair task markdown.

Authoring MCP tools require `fura mcp --author --include-private`. Mutating tools default to dry-run behavior and return `isError: true` if a write is requested without `confirmed: true`.

## Author Lifecycle

For local source mutations, agents should use `fura author` with `--dry-run --json` before writing and `--yes --json` only after confirmation:

```bash
fura author new docs/proposed-page --title "Proposed page" --dry-run --json
fura author edit docs/proposed-page --old-text "Draft" --new-text "Reviewed draft" --dry-run --json
fura author edit docs/proposed-page --old-text "Draft" --new-text "Reviewed draft" --yes --json
fura author publish docs/proposed-page --dry-run --json
fura author publish docs/proposed-page --yes --json
fura author archive docs/proposed-page --dry-run --json
fura author archive docs/proposed-page --yes --json
```

Lifecycle responses include operation id, target path, mount id, previous/resulting visibility, changed files, diagnostics, diff preview, and next actions. Transition responses also include `publication_impact` so agents can see whether navigation, search, export, and public retrieval/LLM surfaces are affected before writing. `fura author edit` uses exact source span replacement, so run `fura author status` or `author_read_source` first and pass the exact `--old-text` value to avoid stale edits.

## Approval Behavior

Recipe steps include `dry_run` and `requires_confirmation` flags in JSON. Agents should run dry-run steps before writes and pause for explicit approval before any step marked `requires_confirmation`.
