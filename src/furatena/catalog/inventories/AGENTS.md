<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: inventories

Keep DCP and Sphinx inventories typed, deterministic, provenance-aware, and safe across mount and visibility boundaries.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Inventory import, export, link, and documentation inventory contracts remain compatible. | P1 | machine-backed | `uv run pytest tests/test_chirp_docs_link_and_inventory_contracts.py tests/test_docs_inventory.py -q` (`inventory-suite`) |

## Guardrails

- Inventory identity, targets, anchors, versions, serialization, and cache invalidation remain stable and diagnosable.
- Private or untrusted inventory data never becomes a public cross-reference by accident.

## Edges

- resolved-by → **references** (cross-project links)
- exports → **catalog** (inventory artifacts)

## Owns

- **code:** `src/furatena/catalog/inventories/`
- **tests:** `tests/test_chirp_docs_link_and_inventory_contracts.py`, `tests/test_docs_inventory.py`
- **docs:** `docs/DCP.md`
