<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: content

Keep published Furatena and mounted Chirp documentation source accurate, audience-safe, navigable, and distinct from generated site output.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Published source content retains valid IR, content lint, visibility, and documented user journeys. | P1 | machine-backed | `uv run pytest tests/test_chirp_docs_content_ir.py tests/test_chirp_docs_content_lint.py tests/test_docs_journeys.py -q` (`content-suite`) |

## Guardrails

- Front matter, links, directives, navigation, audience, edition, and visibility agree with catalog policy and source-backed behavior.
- Docs distinguish shipped behavior, compatibility promises, pilots, roadmap, and generated reference material.

## Edges

- ingested-by → **catalog** (catalog pipeline)
- grounded-by → **docs** (design and operating notes)

## Owns

- **code:** `content/`
- **tests:** `tests/test_chirp_docs_content_lint.py`, `tests/test_docs_journeys.py`, `tests/test_visibility_audit.py`
- **docs:** `content/`
