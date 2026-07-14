<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: docs

Keep architecture, security, compatibility, CI, deployment, publication, and migration documents source-backed and operationally precise.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Documentation quality, generated references, and support-policy claims remain source-backed. | P1 | machine-backed | `uv run pytest tests/test_docs_quality.py tests/test_docs_reference.py tests/test_support_policy.py -q` (`docs-suite`) |

## Guardrails

- Every public field, command, route, schema, compatibility, performance, and security claim traces to code or focused proof.
- Plans and pilots are labeled as such; generated references and inventories are refreshed with repository commands.

## Edges

- informs → **content** (published guides)
- proved-by → **tests** (drift and journey checks)

## Owns

- **code:** `docs/`
- **tests:** `tests/test_docs_quality.py`, `tests/test_docs_reference.py`, `tests/test_support_policy.py`
- **docs:** `docs/`, `README.md`
