<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Agent Constitution — Furatena

Ordinary work: use this root map plus only scoped maps on the target path.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Pillars

- Furatena is a hypermedia documentation catalog: one content graph serves live Chirp views, frozen catalogs, static exports, PDF, search, DCP, CLI, and agent surfaces.
- The dual-IR pipeline preserves source meaning separately from rendered HTML; adapters normalize formats before rendering and generated artifacts retain provenance.
- Public, draft, private, protected, and archived visibility is a cross-surface security contract, not a template preference.
- Deterministic builds, actionable diagnostics, schema-versioned contracts, and generated-reference drift checks are product behavior.
- Furatena runs on free-threaded CPython 3.14 with `PYTHON_GIL=0`; shared state has explicit ownership, synchronization, and lifecycle.
- CLI, browser, static, DCP, MCP, publication, preview, and documentation contracts move together when a public surface changes.

## Search Discipline

- At task start, read the root map before repository discovery; never locate instructions by inventorying every AGENTS.md in the repository.
- Before reading or searching content beneath a path, open the nearest scoped map on that path; add another map only when the investigation crosses into its scope.
- If the request names an exact file, symbol, schema, route, command, fixture, or failing test, inspect that target before searching elsewhere.
- Search progressively: likely files, then filename or import discovery, then scoped content search; expand repository-wide only when scoped evidence fails or proves a cross-cutting dependency, and state the reason.
- Treat 10 commands or 12 content-exposed files as a strategy checkpoint, never as a hard stopping limit; record the current frontier, remaining uncertainty, and whether to continue or pivot.
- For import, dependency, registration, or call-chain bugs, prove graph closure with a bounded static traversal through ancestor package initializers and repository-local module-level imports; stop at function/class bodies, TYPE_CHECKING blocks, and classified external dependencies.
- For parsing, rendering, visibility, publication, or export bugs, prove pipeline closure for the named value: source form, adapter/parser, Content IR, graph or policy transform, render context, reached output surfaces, and focused contract tests.
- Do not use repository-wide dependency, lockfile, documentation, generated-output, or workflow searches to establish closure; broaden only after the bounded graph identifies a cross-surface contract.

## Operating Rules

- Use free-threaded CPython 3.14 with `PYTHON_GIL=0` and install development dependencies through `uv sync --group dev`.
- Preserve unrelated working-tree changes, stage only explicit paths, and never use destructive Git cleanup commands.
- Do not take, implement, close, or batch-plan issues labeled `good first issue` or titled `[GF]`; they are reserved for external contributors.
- `ask stewards`, `bugbash`, `review swarm`, `steward synthesis`, `audit docs`, `content audit`, and `accuracy pass` are explicit review triggers: open the protocol, consult independent affected maps, preserve dissent, and synthesize with evidence.
- Generated references, public-surface inventories, frozen catalogs, and exported output are not source-of-truth; update source/config and use repository commands to regenerate them.
- Generated output under `app/public/`, `app/frozen/`, `app/.preview/`, `pdf-proof/`, `build/`, and `dist/` is not edited as product source.
- Keep browser, static, CLI, DCP, MCP, publication, preview, PDF, and agent contracts aligned whenever a changed value reaches those surfaces.
- Treat warnings, exemptions, snapshots, score thresholds, diagnostic budgets, and coverage baselines as ratchets: new debt fails and resolved debt is removed deliberately.
- Before finalizing agent-authored public files, remove customer names, private people or project names, private quotes, endpoints, and internal scale or cost figures.
- No silent exception, unexplained type-ignore, vague error, speculative config, hand-edited generated fact, snapshot refresh without sensitivity proof, or adjacent refactor unless it is the fix.

## Network

| Steward | Map | Invariants | Automated backing |
| --- | --- | --- | --- |
| app | `app/AGENTS.md` | 1 | 100% |
| autodoc | `src/furatena/catalog/autodoc/AGENTS.md` | 1 | 100% |
| benchmarks | `benchmarks/AGENTS.md` | 1 | 100% |
| catalog | `src/furatena/catalog/AGENTS.md` | 1 | 100% |
| cli | `src/furatena/cli/AGENTS.md` | 1 | 100% |
| cli_commands | `src/furatena/cli/commands/AGENTS.md` | 1 | 100% |
| content | `content/AGENTS.md` | 1 | 100% |
| directives | `src/furatena/catalog/directives/AGENTS.md` | 1 | 100% |
| docs | `docs/AGENTS.md` | 1 | 100% |
| evals | `src/furatena/catalog/eval_datasets/AGENTS.md` | 1 | 100% |
| examples | `examples/AGENTS.md` | 1 | 100% |
| fixtures | `src/furatena/catalog/fixtures/AGENTS.md` | 1 | 100% |
| furatena_theme | `src/furatena/themes/furatena/AGENTS.md` | 1 | 100% |
| github | `.github/AGENTS.md` | 1 | 100% |
| inventories | `src/furatena/catalog/inventories/AGENTS.md` | 1 | 100% |
| lagoon | `src/furatena/themes/lagoon/AGENTS.md` | 1 | 100% |
| migrations | `src/furatena/catalog/migrate/AGENTS.md` | 1 | 100% |
| package | `src/furatena/AGENTS.md` | 1 | 100% |
| references | `src/furatena/catalog/references/AGENTS.md` | 1 | 100% |
| roles | `src/furatena/catalog/roles/AGENTS.md` | 1 | 100% |
| root | `AGENTS.md` | 8 | 87% |
| schemas | `src/furatena/catalog/schemas/AGENTS.md` | 1 | 100% |
| scripts | `scripts/AGENTS.md` | 1 | 100% |
| sources | `src/furatena/catalog/sources/AGENTS.md` | 1 | 100% |
| templates | `src/furatena/catalog/_templates/AGENTS.md` | 1 | 100% |
| tests | `tests/AGENTS.md` | 1 | 100% |
| themes | `src/furatena/themes/AGENTS.md` | 1 | 100% |

## Protects (constitution)

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Generated steward maps fail validation when stale, uncovered, over budget, evidence-rotted, or falsely wired to checks. | P1 | machine-backed | `uv run pytest tests/stewards -q` (`steward-tools`) |
| Formatting, lint, hygiene, typing boundaries, diagnostic ratchets, and core unit contracts remain green under free-threaded Python. | P1 | machine-backed | `make ci-fast` (`fast`) |
| Author, content, route, template, response, generated-reference, and public contract drift remains blocked. | P0 | machine-backed | `make ci-contract` (`contract`) |
| Foundational graph, access, export, and loader coverage cannot fall below owned module ratchets. | P1 | machine-backed | `make ci-coverage` (`coverage`) |
| Static and frozen artifacts preserve URL correctness and exclude draft, private, protected, and archived source canaries. | P0 | machine-backed | `make ci-export` (`export`) |
| MCP resources, agent contracts, safety lint, and deterministic evaluation remain synchronized. | P0 | machine-backed | `make ci-agent` (`agent`) |
| Wheel and sdist contents, isolated installs, package metadata, entry points, and shipped assets remain reproducible. | P0 | machine-backed | `make ci-release` (`release`) |
| Content IR remains the semantic source of truth while HTML IR remains a render artifact used only where presentation requires it. | P0 | manual | docs/DUAL_IR.md · `## Target architecture` |

## Stop & Ask

- A change alters public Python API, CLI commands or exit behavior, DCP/MCP shapes, schema versions, route manifests, theme entry points, package data, optional formats, or compatibility policy.
- A change alters visibility, tenancy, trusted gateway identity, author authorization, capability policy, preview security, publication approval, or private-content export behavior.
- A change changes Content IR or graph identity, source adapter semantics, reference resolution, URL/base-path rules, lifecycle state, cache keys, atomic publication, or operation leases.
- A change affects generated CLI/config references, public-surface inventory, frozen/static/PDF parity, release workflow permissions, provenance, trusted publishing, or deployment defaults.
- A hot-path parser, graph, search, render, freeze, export, or retrieval change lacks a measurement plan; a concurrency change lacks explicit shared-state and GIL-disabled reasoning.
- Tests and code disagree, a reported bug cannot be reproduced, or a public-contract decision remains unresolved.
- When public behavior depends on unresolved product choices, identify only the minimum blocking decisions and stop before designing collateral, compatibility behavior, or migration policy.
- An irreversible operation, deletion, release action, external write, credential-bearing action, or coordinated downstream change is required.

## Done Criteria

- Run the narrowest relevant pytest targets first; run `make test` for broad or release-class changes.
- Before handoff run `make ci-fast`, then each affected lane from `docs/CI.md` such as `make ci-contract`, `make ci-export`, `make ci-agent`, `make ci-pdf-proof`, or `make ci-release`.
- Run `make format-check`, `make lint`, and `make ty-ratchet` for Python changes; do not add ignores, exemptions, or baseline increases merely to pass.
- Visibility or tenancy changes prove allowed and forbidden audiences across every reached live, static, frozen, search, PDF, inventory, DCP, and agent surface.
- Source, IR, graph, render, and export changes test malformed input, deterministic output, provenance, incremental behavior, and every reached format or channel.
- Public behavior moves with docs, generated references, schemas/fixtures, examples, benchmarks, and release collateral when required, or records an explicit no-impact rationale.
- Run `python .stewards/project.py --check` and `python .stewards/verify.py --coverage` after steward maintenance.
- Every accepted steward finding names proof and collateral or an explicit no-impact reason; user-facing errors name the surface and recovery action.

---

Explicit review/audit only: [.stewards/PROTOCOL.md](.stewards/PROTOCOL.md). Steward maintenance only: [.stewards/manifest.toml](.stewards/manifest.toml), then `python .stewards/verify.py --coverage`.
