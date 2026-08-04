<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: vanilla_layout

Keep the packaged vanilla layout complete, low-asset, accessible, and usable without client-side enhancement.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| The packaged vanilla layout remains complete, progressive, accessible, and presentation-only across output surfaces. | P0 | machine-backed | `uv run pytest tests/test_builtin_layouts.py tests/test_chirp_docs_theming.py tests/test_chirp_docs_template_stack.py tests/test_theme_lint.py tests/test_theme_pack.py tests/test_theme_preset.py -q` (`theme-suite`) |

## Guardrails

- Native navigation and GET search remain functional without JavaScript across full, fragment, static, and PDF rendering.
- The neutral stylesheet preserves focus visibility, responsive reflow, reduced motion, contrast, and print behavior.

## Edges

- packaged-by → **themes** (presentation discovery and package data)
- consumes → **templates** (typed render contexts)

## Owns

- **code:** `src/furatena/themes/vanilla/`
- **tests:** `tests/test_builtin_layouts.py`
- **docs:** `docs/PRESENTATION_PACKS.md`, `src/furatena/themes/vanilla/README.md`
