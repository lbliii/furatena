<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: github

Keep CI, Pages, preview reporting, PDF proof, and release workflows least-privileged, reproducible, and aligned with documented local lanes.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Documented CI and release workflows retain required jobs, permissions, and publishing gates. | P0 | machine-backed | `uv run pytest tests/test_ci_lanes.py tests/test_release_publishing.py -q` (`github-suite`) |

## Guardrails

- Workflow jobs call repository-owned commands, pin permissions deliberately, and isolate untrusted code from credentials and publishing identities.
- Pages and release gates, artifact retention, OIDC provenance, tag validation, and concurrency behavior move with `docs/CI.md` and `docs/RELEASING.md`.

## Edges

- invokes → **scripts** (repository checks)
- publishes → **package** (release artifacts)

## Owns

- **code:** `.github/workflows/`
- **tests:** `tests/test_ci_lanes.py`, `tests/test_release_publishing.py`
- **docs:** `docs/CI.md`, `docs/RELEASING.md`, `docs/PR_PREVIEW_SECURITY.md`
