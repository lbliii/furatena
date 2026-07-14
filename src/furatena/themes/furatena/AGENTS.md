<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: furatena_theme

Keep Furatena's primary visual system, icons, CSS scopes, responsive shell, and accessibility aligned with semantic templates.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| The primary theme remains compatible with semantic catalog templates and full/fragment rendering. | P1 | machine-backed | `uv run pytest tests/test_chirp_docs_theming.py tests/test_chirp_docs_template_stack.py tests/test_theme_lint.py tests/test_theme_pack.py tests/test_theme_preset.py -q` (`theme-suite`) |

## Guardrails

- CSS remains scoped to documented theme boundaries and does not depend on accidental generated markup.
- Icons, contrast, focus, reduced motion, responsive navigation, print/PDF behavior, and dark mode move with visual changes.

## Edges

- styles → **templates** (semantic output)
- mirrored-by → **app** (runtime theme assets)

## Owns

- **code:** `src/furatena/themes/furatena/`
- **tests:** `tests/test_chirp_docs_theming.py`, `tests/test_chirp_docs_template_stack.py`
- **docs:** `docs/THEMING.md`, `src/furatena/themes/furatena/assets/css/CSS_SCOPING_RULES.md`
