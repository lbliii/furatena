<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: roles

Keep inline roles, xrefs, glossary terms, and inventory references format-compatible and safely rendered.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Inline role and cross-reference behavior remains covered with directive and resolver contracts. | P1 | machine-backed | `uv run pytest tests/test_chirp_docs_directives.py tests/test_chirp_docs_list_table.py -q` (`directives-suite`) |

## Guardrails

- Role registration, target parsing, display text, unresolved behavior, escaping, and link extraction move together.
- Roles do not smuggle trusted HTML or skip the central reference resolver.

## Edges

- delegates-to → **references** (target resolution)
- parallels → **directives** (author syntax)

## Owns

- **code:** `src/furatena/catalog/roles/`
- **tests:** `tests/test_chirp_docs_directives.py`, `tests/test_chirp_docs_reference_resolution.py`
- **docs:** `docs/AUTHORING.md`
