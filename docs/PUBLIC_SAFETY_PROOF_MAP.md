# Public safety proof map

Issue: [#583](https://github.com/lbliii/furatena/issues/583) — Map P0
public-safety proof across every delivery surface.
Parent epic: [#582](https://github.com/lbliii/furatena/issues/582).

Visibility (`public` | `draft` | `private` | `protected`/`internal`+ACL |
`archived`) is a **cross-surface security contract**, not a template preference.

## Generated output is not source of truth

Do not treat, hand-edit, or cite as policy evidence:

- `app/public/`
- `app/frozen/`
- `app/.preview/`
- `pdf-proof/`
- `build/` / `dist/`
- export/cache trees under `app/.docs-cache*`

Authoritative sources are content + config, catalog code (`access`,
`lifecycle`, `export`, `static_export`, `visibility_audit`, `mcp`, routes),
schemas/fixtures, and **executable tests / Make lanes**. Regenerate artifacts
with repository commands.

## Shared pipeline

```text
Source (.md / .rst / .mdx / .html / autodoc / remote shard)
  → adapter/parser
  → Content IR + body_html
  → catalog graph (DocNode, edges)
  → policy (lifecycle + AccessPolicy + subject)
  → surface producer (live | freeze | static | PDF | MCP | CLI)
  → optional scan_visibility_leaks
```

| Stage | Anchor |
| --- | --- |
| Scan skip | `sources/scanner.py` when `include_private=False` |
| Lifecycle | `lifecycle.is_public_meta` / `visibility_state` |
| Access | `access.can_access` / `AccessService.filter_nodes` |
| Export APIs | `export.*(..., include_private=, subject=)` |
| Artifact audit | `visibility_audit.scan_visibility_leaks` |
| Projection matrix | `public_projection.PUBLIC_PROJECTION_SURFACES` |

## Surface → proof

| Surface | Allowed / forbidden proof | Gaps |
| --- | --- | --- |
| Live HTML routes | `tests/test_chirp_docs_rbac.py`, `tests/test_access_isolation.py`, `tests/test_preview_security.py` | No single matrix for every route kind × four forbidden boundaries |
| Fragments (HX) | Presentation/author boost contracts | **Missing** dedicated private-slug fragment canary |
| Static export | `test_fura_cli_standalone.py` export canaries; `make ci-export` Pages build scan + `artifact_audit` | Fixture canaries not selected by `make ci-agent` `-k` filter |
| Frozen catalog | `test_freeze_excludes_draft_pages_from_frozen_ir_and_preview` | Private/protected/archived less explicit than draft |
| Search / suggest / semantic | RBAC, access isolation, `tests/test_retrieval_conformance.py`, author-mode public filter | `/errors/suggest` audience not named |
| PDF | `tests/test_visibility_audit.py`, `tests/test_public_projection.py`, `tests/test_pdf_proof.py` | `make ci-pdf-proof` is fidelity-first, not full audience matrix |
| Inventories / `objects.inv` | Link/inventory contracts; export scan if tokens leak into tree | **Thin** — no planted private role → absent `objects.inv` proof |
| DCP `catalog.json` | RBAC, author-mode filter, retrieval conformance, `agent_lint` | Schema fixtures are format-only |
| `llms.txt` / `llms-full.txt` | Author-mode filter, export canaries, develop sample | Mount mirrors less duplicated |
| `tools.json` | Export canaries, author-mode counts, agent lint | — |
| MCP resources/tools | RBAC, access isolation, retrieval conformance, MCP Apps `include_private: false` | Not every tool has a private-canary denial case |
| CLI author / query / check | `tests/test_public_projection.py`, check draft-link diagnostics | `fura query` relies on catalog/MCP proofs |
| Railway preview | `tests/test_preview_security.py`, Railway controller tests | Identity/auth heavier than full canary matrix |
| `/develop/` previews | Landing + author-mode sample Secret assertion | Not every develop export id has a canary |
| Channel / version / editions | `tests/test_version_artifacts.py`, `tests/test_edition_routing.py`, federation publish | Planned channels must not advertise private URLs |
| Sitemap | Author-mode + export canaries | Mount sitemap less duplicated |
| Nav / sidebar / breadcrumbs / page text / markdown | `tests/test_public_projection.py` surface matrix | Sparse outside projection inspector |
| Publication / content-generation builders | `tests/test_publication_artifacts.py`, `tests/test_content_generation.py` | Adjacent to HTTP delivery; still P0 for promoted roots |

## Duplicate or misplaced proof

1. `tests/test_visibility_audit.py` is authoritative for scanner behavior but
   was historically absent from focused `make ci-*` target lists.
2. Export/freeze/author public-filter mega-tests live in
   `tests/test_fura_cli_standalone.py` and are dropped by
   `make ci-agent`'s `-k "agent or mcp or evals"` filter.
3. `make ci-export` pytest smoke does not assert visibility; audience
   fail-closed happens inside export during `pages-build`.
4. RBAC, access isolation, and retrieval conformance intentionally overlap —
   layered subjects (ACL, gateway spoofing, retrieval parity).
5. Public projection (publish impact) and export canaries (artifact absence)
   are complementary; do not collapse them.
6. MCP App UI `visibility` is not content-audience visibility.
7. `artifact_audit` (URL/base-path) ≠ `visibility_audit` (audience canaries).

## Gap backlog (feeds #584 / #585)

| Pri | Gap |
| --- | --- |
| P0 | Own `make ci-public-safety` lane that always runs canary + projection + access proofs |
| P0 | Explicit `objects.inv` audience proof |
| P1 | Fragment (HX) forbidden-audience canary |
| P1 | PDF full draft/private/protected/archived matrix |
| P1 | `/develop/` canary for every develop export id |
| P2 | `/errors/suggest` audience; planned channels cannot leak private URLs |

## Lane ownership

| Lane | Public-safety role today |
| --- | --- |
| `make ci-fast` | Access isolation, retrieval private/archived, RBAC via selected suites |
| `make ci-contract` | Public projection privacy matrix |
| `make ci-coverage` | Branch coverage on `access` / `export` / graph / loader |
| `make ci-export` | Pages build canary scan + URL artifact audit |
| `make ci-agent` | Agent/MCP slice; misses export canary CLI tests |
| `make ci-pdf-proof` | PDF fidelity + protected fixture presence |
| `make ci-public-safety` | Focused visibility + projection + access + map ratchet |

Root Protect: static and frozen artifacts exclude draft, private, protected,
and archived source canaries → `make ci-export`.
