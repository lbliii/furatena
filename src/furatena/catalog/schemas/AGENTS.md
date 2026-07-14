<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: schemas

Keep shipped JSON schemas versioned, strict enough to catch drift, and synchronized with Python models, fixtures, CLI, and docs.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Shipped schemas reject drift and remain synchronized with serialized contract snapshots. | P0 | machine-backed | `uv run pytest tests/test_capability_schemas.py tests/test_preview_schemas.py tests/test_publication_schemas.py tests/test_validation_snapshots.py -q` (`schema-suite`) |

## Guardrails

- Schema changes classify compatibility and move with validators, example payloads, snapshots, and migration notes.
- Do not loosen required fields or additional-property policy merely to accept an implementation regression.

## Edges

- validates → **fixtures** (contract examples)
- describes → **catalog** (serialized public contracts)

## Owns

- **code:** `src/furatena/catalog/schemas/`
- **tests:** `tests/test_capability_schemas.py`, `tests/test_preview_schemas.py`, `tests/test_publication_schemas.py`
- **docs:** `docs/DCP.md`, `docs/PUBLICATION_WORKFLOW.md`
