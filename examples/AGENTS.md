<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: examples

Keep examples copyable, secure by default, version-compatible, and representative of supported Furatena workflows.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Governed preview starter repositories remain complete and executable. | P1 | machine-backed | `uv run pytest tests/test_starter_repositories.py -q` (`examples-suite`) |

## Guardrails

- Examples use public APIs and production-shaped configuration rather than repository-private shortcuts.
- Starter changes prove scaffold, check, freeze, export, deployment, and documentation paths that users will run.

## Edges

- demonstrates → **docs** (documented workflows)
- deployed-by → **github** (example workflows)

## Owns

- **code:** `examples/`
- **tests:** `tests/test_starter_repositories.py`, `tests/test_github_pages_tutorial.py`
- **docs:** `examples/`, `docs/GOVERNED_PR_PREVIEWS.md`
