<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: scripts

Keep repository checks, builds, benchmarks, release helpers, preview reporting, and artifact verification deterministic and actionable.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Typing, coverage, and live-artifact verification scripts preserve failure-sensitive ratchets. | P1 | machine-backed | `uv run pytest tests/test_ty_diagnostic_ratchet.py tests/test_core_coverage_policy.py tests/test_verify_live_artifacts.py -q` (`scripts-suite`) |

## Guardrails

- Scripts fail closed on contract drift, name the file or surface to fix, and avoid hidden network, credential, or destructive side effects.
- Baselines and generated outputs have explicit check/update modes; update modes never run accidentally in CI.

## Edges

- invoked-by → **github** (CI workflows)
- drives → **benchmarks** (measurement harnesses)

## Owns

- **code:** `scripts/`
- **tests:** `tests/test_ty_diagnostic_ratchet.py`, `tests/test_core_coverage_policy.py`, `tests/test_verify_live_artifacts.py`
- **docs:** `docs/CI.md`, `docs/PERFORMANCE.md`
