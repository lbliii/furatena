<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: templates

Keep built-in Kida templates semantically aligned with render contexts, view kinds, safe HTML boundaries, and full/fragment response contracts.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Built-in templates preserve Kida component seams, view models, and response-shape contracts. | P0 | machine-backed | `uv run pytest tests/test_chirp_docs_theming.py tests/test_chirp_docs_template_stack.py tests/test_theme_lint.py tests/test_theme_pack.py tests/test_theme_preset.py -q` (`theme-suite`) |

## Guardrails

- Template variables come from typed render contexts; missing values fail with actionable diagnostics.
- Full pages, fragments, static output, and theme overrides preserve semantic markup and required assets.

## Edges

- styled-by → **themes** (theme packs)
- renders → **catalog** (view models)

## Owns

- **code:** `src/furatena/catalog/_templates/`
- **tests:** `tests/test_chirp_docs_template_stack.py`, `tests/test_chirp_docs_views.py`, `tests/test_kida_component_seams.py`
- **docs:** `docs/THEMING.md`, `docs/VIEWS.md`
