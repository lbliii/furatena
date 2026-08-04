# CI public-safety map

This document satisfies [#583](https://github.com/lbliii/furatena/issues/583): a
bounded source-to-output ownership map for every anonymous public and agent
visibility guarantee. It supports epic [#582](https://github.com/lbliii/furatena/issues/582)
and saga [#577](https://github.com/lbliii/furatena/issues/577).

Generated artifacts (`app/public/`, `app/frozen/`, PDFs, inventories) are never
treated as source of truth. Proof always traces back to source files, lifecycle
policy, and access control before naming reached outputs.

## Policy spine

| Stage | Module | Role |
| --- | --- | --- |
| Source scan | `src/furatena/catalog/sources/scanner.py` | Drops non-public sources unless `include_private=True` |
| Catalog load | `src/furatena/catalog/loader.py`, `registry.py` | `DocNode` graph, mounts, editions |
| Lifecycle | `src/furatena/catalog/lifecycle.py` | `visibility_state`, `is_public_meta`, lifecycle lint |
| Access | `src/furatena/catalog/access.py` | `accessible_nodes`, `can_access`, role/team gates |
| Render | `src/furatena/catalog/render.py`, `render_context.py` | Markdown → HTML + Content IR |
| Canary audit | `src/furatena/catalog/visibility_audit.py` | Derive tokens; scan HTML, JSON, PDF, `.inv` trees |

Canary boundaries: **draft**, **private**, **protected** (internal/team), and
**archived** — see `tests/test_visibility_audit.py`.

Umbrella inspector: `PublicProjectionInspector` in
`src/furatena/catalog/public_projection.py` simulates publish, unpublish, and
archive across all projection surfaces without mutating source. Spec:
`docs/PUBLIC_PROJECTION_INSPECTION.md`.

## Surface catalog

Each row names authoritative proof for anonymous or agent audiences. “CI lane” is
the Make target that should fail first when the surface regresses.

| Surface | Source → transform → artifact | Authoritative tests | CI lane |
| --- | --- | --- | --- |
| Live HTML pages | `content/**` → `DocsRenderer` → `DocsApp` routes | `test_public_projection` (`anonymous_html`); `test_chirp_docs_rbac`; `test_retrieval_conformance` | contract, browser |
| Route availability | `accessible_nodes` + `route_registrars.py` | `test_public_projection` (`route_availability`); `test_edition_routing` | contract |
| Navigation / sidebar / breadcrumbs | `render_context.py` | `test_public_projection` (`navigation`, `sidebar`, `breadcrumbs`) | contract |
| Search HTML / HTMX | `search_experience.py`, `search.py` | `test_chirp_docs_rbac`; `test_public_projection` (`search`); `test_chirp_docs_response_conformance` | browser, contract |
| Search sidecar `search.json` | `export.search_json()` | `test_visibility_audit`; `test_retrieval_conformance`; `test_access_isolation`; `test_chirp_docs_catalog_surfaces` | export |
| Search suggestions | `/search/suggest` handlers | `test_public_projection` (`suggestions`) | contract |
| DCP `catalog.json` | `export.catalog_graph()` → freeze | `test_chirp_docs_rbac`; `test_retrieval_conformance`; `test_edition_shards`; `test_public_projection` (`dcp_catalog`) | export |
| DCP `structure.json` | `structure_index.build_structure_index()` | `test_public_projection` (`dcp_structure`); RBAC registry tests | export |
| DCP `semantic.json` | `semantic.semantic_index_json()` | `test_retrieval_conformance` (MCP/browser parity) | export |
| Channels `channels.json` | `channel_manifest.py` | `test_public_projection` (`channels`); `test_federation_artifacts` | export |
| Static export tree | `static_export.export_static_site()` → `app/public/**` | `test_chirp_docs_static_export`; `test_fura_cli_standalone` export tests; mandatory `visibility_audit` in `static_export.py` | **export** |
| Frozen IR | `freeze.py` → `app/frozen/**` | `test_edition_shards`; `test_content_generation`; `test_fura_cli_standalone` freeze tests | export |
| Edition shards | `edition_shards.freeze_edition_shards()` | `test_edition_shards` (private canary excluded) | contract |
| Sitemap | `sitemap.py`, `shard_discovery.py` | `test_public_projection` (`sitemap`) | export |
| PDF output | `pdf_export.py`, `scripts/pdf_proof.py` | `test_pdf_proof`; `test_public_projection` (`pdf`); `test_visibility_audit` (PDF text) | pdf-proof, export |
| Page text / Markdown sidecars | static export collectors | `test_public_projection` (`page_text`, `page_markdown`); `test_builtin_layouts` | export |
| Agent `llms.txt`, `llms-full.txt`, `tools.json` | `export.py` agent helpers | `test_visibility_audit`; `test_chirp_docs_catalog_surfaces`; `test_public_projection`; `test_agent_contract_diff` | export, agent |
| MCP resources and tools | `mcp.py` (`include_private=False`, anonymous subject) | `test_access_isolation`; `test_retrieval_conformance`; `test_chirp_docs_rbac`; `test_public_projection` (`mcp_public_resources`); `fura check --agent-only` | **agent** |
| MCP Apps catalog search | `mcp_apps.py` | `test_mcp_apps_contract`; `test_mcp_catalog_search_app` | agent |
| Inventories `objects.inv` | `inventories/sphinx.py` | `test_visibility_audit` (format parser); link contracts in `test_chirp_docs_link_and_inventory_contracts` | export (indirect) |
| Federation published shards | `federation_artifacts.py`, `federation_publish.py` | `test_federation_artifacts` (schema rejects restricted records) | contract |
| Publication artifacts | `publication_artifacts.py` + `_privacy_scan()` | `test_publication_artifacts`; `test_publication_adversarial_conformance` | release path |
| Content generation bundle | `content_generation.py` + `_privacy_scan()` | `test_content_generation` | — |
| Presentation tooling | `presentation_tooling.py` + `scan_visibility_leaks` | `test_presentation_tooling`; `test_presentation_pack` | — |
| **All projection surfaces** | `public_projection.py` read-only simulation | `test_public_projection.py` (19 surfaces + canary scan) | contract |

## Per-surface notes

### Static export (primary deploy gate)

`static_export.py` runs `visibility_canaries()` and `scan_visibility_leaks()` on
the complete tree before returning. Failure raises `StaticExportVisibilityError`.
`make ci-export` builds a production-shaped Pages tree and runs
`artifact_audit.py` for URL/canonical integrity — complementary to visibility,
not duplicate.

### Public projection (umbrella simulation)

`test_public_projection.py` is the single test that enumerates every named
surface in one anonymous simulation. Prefer extending projection when adding a
new public sidecar rather than relying on generated HTML alone.

### Browser tier (shape, not canary-primary)

`make ci-browser` proves search, navigation, authoring, and responsive flows.
`test_chirp_docs_response_conformance.py` validates hypermedia shape, not
forbidden canary strings. Live HTML leak proof is owned by public projection and
static export until a dedicated Playwright canary grep lands in #584.

### PDF proof

`make ci-pdf-proof` uses fixed public/protected sentinels on the stress page.
Dynamic per-catalog canaries come from export and projection paths, not from the
PDF lane alone.

### Freeze path

`freeze.py` does not scan canaries itself. Callers (`static_export`,
`publication_artifacts`, `content_generation`, `presentation_tooling`) scan
downstream bundles before promotion or deploy.

## Proof gaps (planned)

| Gap | Owner lane | Notes |
| --- | --- | --- |
| Browser canary grep on public URLs | browser (#584) | Projection covers simulation today |
| Dedicated `objects.inv` private-symbol integration | export | Scanner unit test exists; no end-to-end `.inv` canary |
| Federation publish E2E with dynamic canaries | contract | Schema rejection tests exist today |
| `semantic.json` as named projection surface | export | Covered by retrieval parity only |
| Suggestions-only sidecar test | contract | Projection-only today |
| Freeze-time canary scan | export | Relies on downstream scanners |

## Duplicate and misplaced proof

| Pattern | Locations | Assessment |
| --- | --- | --- |
| Visibility canary scan | `static_export.py`, `public_projection.py`, `publication_artifacts.py`, `content_generation.py`, `presentation_tooling.py` | Intentional defense-in-depth; shared `visibility_audit` primitives |
| Unit vs integration canaries | `test_visibility_audit.py`, `test_fura_cli_standalone` export JSON, `test_public_projection.py` | Unit = format/boundary; CLI = wiring; projection = all surfaces |
| MCP / search / catalog isolation | `test_access_isolation.py`, `test_retrieval_conformance.py`, `test_chirp_docs_rbac.py` | Different angles: tenant boundaries, cross-surface parity, mount policy |
| `artifact_audit` vs `visibility_audit` | both run in `ci-export` | URL integrity vs content leaks — keep paired |
| Publication privacy scan | `publication_artifacts.py` vs `visibility_canaries()` | Same algorithm family; consolidation is refactor, not a proof hole |

## Collateral surfaces (explicit no-impact)

| Surface | Why out of scope |
| --- | --- |
| Author routes (`/docs/_author/*`) | Trusted session; `ServeMode.AUTHOR`; proofs in `test_author_authorization.py` |
| Public projection inspect action | Publisher-gated simulation; does not expose drafts on anonymous routes |
| PR preview (`ServeMode.PREVIEW`) | Token/auth gated; `test_preview_security.py` |
| Preview auth / grant runtime | Operator broker; not anonymous delivery |
| Content refresh internal API | Operator-only; not in static export |
| Develop index (`/develop/`) | HTML wrapper over already-filtered public JSON sidecars |
| Health / readiness | No catalog content |
| Theme vendor assets | No source-derived confidential text |
| Private-image distribution | Commercial container artifact; not docs catalog delivery |
| Agent contract fixtures | Golden schemas; contract shape, not runtime leaks |

## Source-of-truth hierarchy

1. **Source files** (`content/**`, front matter) — truth.
2. **Policy modules** (`lifecycle.py`, `access.py`) — eligibility.
3. **Transform / render** — projection only.
4. **Generated artifacts** — always scanned downstream; never cited as proof inputs.

## CI lane ownership (public-safety subset)

| Lane | Public-safety scope |
| --- | --- |
| `make ci-fast` | RBAC/access unit tests, `visibility_audit` unit, projection schemas |
| `make ci-contract` | RBAC, response conformance, retrieval conformance, federation schema, **public projection** |
| `make ci-export` | Static export tests, Pages build, **visibility canaries on full tree**, URL crawl |
| `make ci-browser` | Hypermedia smoke — not canary-primary |
| `make ci-agent` | `fura check --agent-only`, MCP/agent pytest subset |
| `make ci-pdf-proof` | PDF sentinel proof on stress page |
| `make ci-release` | Scaffolded app freeze + export smoke |

Wave 2 follow-up ([#584](https://github.com/lbliii/furatena/issues/584)): extract
`make ci-public-safety` from the rows above without weakening export or release
proof until impact routing lands in wave 3.
