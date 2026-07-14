<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: themes

Keep theme discovery, package entry points, presets, asset ownership, overrides, and compatibility explicit.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Theme packs, presets, lint, package paths, and catalog rendering contracts remain synchronized. | P0 | machine-backed | `uv run pytest tests/test_chirp_docs_theming.py tests/test_chirp_docs_template_stack.py tests/test_theme_lint.py tests/test_theme_pack.py tests/test_theme_preset.py -q` (`theme-suite`) |

## Guardrails

- Theme packs declare stable identity and assets; missing, colliding, or unsafe paths fail before rendering.
- Default and optional themes share semantic view contracts even when their presentation differs.

## Edges

- contains → **furatena_theme** (primary product theme)
- contains → **lagoon** (packaged Lagoon theme)

## Owns

- **code:** `src/furatena/themes/`
- **tests:** `tests/test_theme_pack.py`, `tests/test_theme_preset.py`, `tests/test_theme_lint.py`
- **docs:** `docs/THEMING.md`
