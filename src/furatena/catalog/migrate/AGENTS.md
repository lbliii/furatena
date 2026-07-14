<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: migrations

Keep migration analysis conservative, explainable, idempotent, and separate from destructive source rewriting.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| MDX migration and migration playbooks retain deterministic analysis and remediation coverage. | P1 | machine-backed | `uv run pytest tests/test_chirp_docs_mdx_migration.py tests/test_migration_playbooks.py -q` (`migration-suite`) |

## Guardrails

- Reports distinguish safe automatic rewrites, review-required remediation, unsupported constructs, and unchanged content.
- Migration output preserves source locations and offers a rollback or dry-run story before any mutation.

## Edges

- analyzes → **sources** (source formats)
- invoked-by → **cli_commands** (migrate command)

## Owns

- **code:** `src/furatena/catalog/migrate/`
- **tests:** `tests/test_chirp_docs_mdx_migration.py`, `tests/test_migration_playbooks.py`
- **docs:** `docs/MIGRATION_PILOTS.md`, `docs/COMPATIBILITY.md`
