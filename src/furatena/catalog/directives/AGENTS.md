<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: directives

Keep directive syntax, validation, IR effects, safe HTML boundaries, and Kida rendering consistent across formats.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Supported directives and list tables retain parsed, rendered, linked, and malformed-input coverage. | P1 | machine-backed | `uv run pytest tests/test_chirp_docs_directives.py tests/test_chirp_docs_list_table.py -q` (`directives-suite`) |

## Guardrails

- Directive registration, parsing, node shape, HTML output, link extraction, errors, docs, and tests move together.
- Includes remain bounded and provenance-aware; raw HTML and embeds never bypass trust or visibility policy.

## Edges

- extends → **catalog** (Content IR and rendering)
- composes → **roles** (inline references)

## Owns

- **code:** `src/furatena/catalog/directives/`
- **tests:** `tests/test_chirp_docs_directives.py`, `tests/test_chirp_docs_list_table.py`, `tests/test_safe_html_boundaries.py`
- **docs:** `docs/AUTHORING.md`, `docs/SAFE_HTML_BOUNDARIES.md`
