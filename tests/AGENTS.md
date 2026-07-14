<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: tests

Keep tests as deterministic executable contracts for formats, graph identity, visibility, rendering, publication, CLI, artifacts, and free-threaded operation.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Furatena retains a single exact full-suite entry point under GIL-disabled Python. | P1 | machine-backed | `make test` (`full-tests`) |

## Guardrails

- Escaped bugs gain focused regression tests through public paths; cross-surface bugs assert every reached surface and forbidden leakage.
- Prefer semantic assertions over broad snapshots; snapshot updates prove sensitivity and explain intentional change.
- Network, browser, generated-artifact, and slow tests are isolated in explicit lanes and markers.

## Edges

- verifies → **root** (repository behavior)
- consumes → **fixtures** (versioned contract examples)

## Owns

- **code:** `tests/`
- **tests:** `tests/`
- **docs:** `tests/README.md`, `docs/CI.md`

## Advocate

- Executable bug reports that prove positive behavior and forbidden leakage.
- Narrow checks first, then the owning CI lane and full suite in proportion to risk.

## Do Not

- Bless regressions by weakening assertions, snapshots, thresholds, exemptions, or baselines.
- Depend on incidental ordering, generated IDs, whitespace, or local generated artifacts.
