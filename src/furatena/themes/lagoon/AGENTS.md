<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: lagoon

Keep the packaged Lagoon theme self-contained, accessible, and compatible with the same catalog and static-search contracts as the primary theme.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Lagoon remains a packaged, discoverable theme with tested catalog compatibility. | P1 | machine-backed | `uv run pytest tests/test_builtin_layouts.py tests/test_chirp_docs_theming.py tests/test_chirp_docs_template_stack.py tests/test_theme_lint.py tests/test_theme_pack.py tests/test_theme_preset.py -q` (`theme-suite`) |

## Guardrails

- JavaScript enhancement remains progressive; navigation, search, TOC, and theme selection work from semantic HTML first.
- Package-data changes move with entry-point metadata, asset audits, and isolated distribution smoke.

## Edges

- registered-by → **themes** (theme pack entry point)
- styles → **templates** (catalog views)

## Owns

- **code:** `src/furatena/themes/lagoon/`
- **tests:** `tests/test_chirp_docs_theming.py`, `tests/test_catalog_packaging.py`
- **docs:** `docs/THEMING.md`
