<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: catalog

Keep the catalog graph, dual IR, visibility, lifecycle, render, freeze, export, search, publication, and agent surfaces one coherent system.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Catalog behavior remains coherent across live routes, generated references, response contracts, and static delivery. | P0 | machine-backed | `make ci-contract` (`contract`) |

## Guardrails

- Trace changed values from source through Content IR, graph/policy transforms, render context, and every reached output surface.
- Identity, visibility, base paths, provenance, incremental invalidation, and atomic publication remain deterministic.
- Flat catalog modules stay cohesive; do not create a new subsystem or configuration axis without an explicit boundary decision.

## Edges

- fed-by → **sources** (normalized sources)
- served-by → **app** (live runtime)
- built-from → **content** (repository content)

## Owns

- **code:** `src/furatena/catalog/*.py`
- **tests:** `tests/test_chirp_docs_catalog_surfaces.py`, `tests/test_chirp_docs_graph_structure.py`, `tests/test_chirp_docs_static_export.py`
- **docs:** `docs/DUAL_IR.md`, `docs/DCP.md`, `docs/VIEWS.md`

## Advocate

- One canonical identity and policy path shared by live, frozen, static, PDF, search, DCP, publication, and agent consumers.
- Focused cross-surface regression matrices when changed values cross delivery boundaries.

## Do Not

- Patch only the visible renderer when the underlying IR, graph, policy, or manifest is wrong.
- Introduce parallel truth in generated JSON, HTML, templates, caches, or deployment metadata.
