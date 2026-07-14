<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: sources

Keep filesystem, Git, markdown, MyST, RST, MDX, and HTML sources normalized into stable Content IR without format-specific leakage.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Supported source formats preserve normalized structure, provenance, round-trip meaning, and deterministic discovery. | P0 | machine-backed | `uv run pytest tests/test_chirp_docs_sources.py tests/test_chirp_docs_ast_roundtrip.py -q` (`sources-suite`) |

## Guardrails

- Adapters preserve locations, front matter, links, structure, provenance, and unsupported syntax diagnostics.
- Scanner identity, ignore rules, symlink behavior, ordering, and incremental fingerprints remain deterministic and tenant-safe.

## Edges

- feeds → **catalog** (normalized Content IR)
- analyzed-by → **migrations** (format migration)

## Owns

- **code:** `src/furatena/catalog/sources/`
- **tests:** `tests/test_chirp_docs_sources.py`, `tests/test_chirp_docs_ast_roundtrip.py`, `tests/test_git_edition_discovery.py`
- **docs:** `docs/DUAL_IR.md`, `docs/AUTHORING.md`, `docs/EDITIONS.md`

## Advocate

- Small adapters over one normalized IR with source locations and provenance.
- Malformed-source tests for every supported format.

## Do Not

- Leak parser-library objects past the adapter boundary.
- Silently discard unsupported syntax, front matter, links, or identity.
