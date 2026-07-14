<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: package

Keep the installed Furatena package, public imports, typing marker, dependencies, entry points, and release artifacts coherent.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Installed distributions expose the declared package, fura entry point, typing and package data without checkout leakage. | P0 | machine-backed | `make ci-release` (`release`) |

## Guardrails

- `pyproject.toml`, `uv.lock`, package data, `fura` entry point, README commands, and isolated install smoke move together.
- Optional source formats stay optional and local co-development overrides never enter committed project metadata.

## Edges

- exposes → **cli** (fura entry point)
- published-by → **github** (release workflow)

## Owns

- **code:** `src/furatena/*.py`, `pyproject.toml`, `uv.lock`
- **tests:** `tests/test_catalog_packaging.py`, `tests/test_release_publishing.py`
- **docs:** `README.md`, `docs/RELEASING.md`
