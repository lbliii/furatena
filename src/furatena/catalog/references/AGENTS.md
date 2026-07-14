<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: references

Keep local, mounted, inventory, glossary, and API reference resolution deterministic and rich in actionable context.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Reference resolution preserves local, mounted, inventory, ambiguity, and missing-target behavior. | P0 | machine-backed | `uv run pytest tests/test_chirp_docs_reference_resolution.py -q` (`references-suite`) |

## Guardrails

- Resolution order, ambiguity, missing-target diagnostics, version context, and base paths are explicit contracts.
- A resolved link retains enough provenance to audit which source and inventory supplied it.

## Edges

- consults → **inventories** (external targets)
- serves → **roles** (inline xrefs)

## Owns

- **code:** `src/furatena/catalog/references/`
- **tests:** `tests/test_chirp_docs_reference_resolution.py`
- **docs:** `docs/ROUTES.md`, `docs/DCP.md`
