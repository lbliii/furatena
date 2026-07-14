<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: fixtures

Keep shipped contract fixtures minimal, schema-valid, versioned, and representative of supported producer and consumer behavior.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Preview and publication fixtures remain schema-valid and sensitivity-tested. | P1 | machine-backed | `uv run pytest tests/test_preview_fixtures.py tests/test_publication_fixtures.py tests/test_validation_snapshots.py -q` (`fixtures-suite`) |

## Guardrails

- Fixture changes prove validation sensitivity and never replace production-path assertions with snapshot blessing.
- New versions preserve explicit compatibility and migration expectations.

## Edges

- validated-by → **schemas** (versioned schemas)
- consumed-by → **tests** (contract suites)

## Owns

- **code:** `src/furatena/catalog/fixtures/`
- **tests:** `tests/test_preview_fixtures.py`, `tests/test_publication_fixtures.py`, `tests/test_validation_snapshots.py`
- **docs:** `docs/DCP.md`, `docs/PUBLICATION_WORKFLOW.md`
