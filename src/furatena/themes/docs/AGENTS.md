<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: docs_layout

Keep the packaged docs layout complete, immutable, and synchronized with the proven application templates.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| The packaged docs layout owns every supported view and remains synchronized with the proven application templates. | P0 | machine-backed | `uv run pytest tests/test_builtin_layouts.py tests/test_chirp_docs_theming.py tests/test_chirp_docs_template_stack.py tests/test_theme_lint.py tests/test_theme_pack.py tests/test_theme_preset.py -q` (`theme-suite`) |

## Guardrails

- Every supported view kind resolves without site-owned templates while preserving full and fragment response hooks.
- Application template changes move with the packaged extraction and its byte-for-byte drift proof.

## Edges

- packaged-by → **themes** (presentation discovery and package data)
- extracted-from → **app** (proven documentation templates)

## Owns

- **code:** `src/furatena/themes/docs/`
- **tests:** `tests/test_builtin_layouts.py`
- **docs:** `docs/PRESENTATION_PACKS.md`, `src/furatena/themes/docs/README.md`
