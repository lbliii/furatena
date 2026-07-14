<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: app

Keep the runnable documentation application, source theme, locales, server scripts, freeze inputs, and generated-output boundaries explicit.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| The live application preserves catalog runtime, render-context, and response-conformance behavior. | P0 | machine-backed | `uv run pytest tests/test_chirp_docs_runtime.py tests/test_render_context.py tests/test_chirp_docs_response_conformance.py -q` (`runtime-suite`) |

## Guardrails

- `app/content/`, `app/templates/`, `app/theme/`, and runtime scripts are source; generated cache, preview, frozen, and public trees are not.
- Live and exported routes use the same catalog identity, visibility, base-path, asset, and response contracts.

## Edges

- hosts → **catalog** (live catalog runtime)
- mounts → **content** (documentation sources)
- develops → **furatena_theme** (source theme)

## Owns

- **code:** `app/run`, `app/export`, `app/preview`, `app/templates/`, `app/theme/`, `app/locales/`
- **tests:** `tests/test_chirp_docs_runtime.py`, `tests/test_chirp_docs_response_conformance.py`
- **docs:** `docs/APP-README.md`, `docs/RAILWAY.md`
