<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: autodoc

Keep Python autodoc extraction deterministic, source-attributed, and compatible with the same IR and reference contracts as authored content.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Autodoc inventory and generated reference behavior remains covered through supported source contracts. | P1 | machine-backed | `uv run pytest tests/test_chirp_contract_inventory.py tests/test_docs_reference.py -q` (`autodoc-suite`) |

## Guardrails

- Extraction never imports arbitrary project code when static analysis can answer safely.
- Generated members preserve stable identity, signatures, ordering, provenance, and actionable unsupported-shape diagnostics.

## Edges

- produces → **catalog** (generated catalog nodes)
- linked-by → **references** (API references)

## Owns

- **code:** `src/furatena/catalog/autodoc/`
- **tests:** `tests/test_chirp_contract_inventory.py`, `tests/test_docs_reference.py`
- **docs:** `docs/AUTHORING.md`
