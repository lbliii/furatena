<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: cli

Keep the `fura` facade, parser, lazy command dispatch, structured errors, output channels, and exit behavior stable and scriptable.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| The fura entry point remains importable, scriptable, and runnable outside the checkout. | P0 | machine-backed | `uv run pytest tests/test_cli_entrypoint_smoke.py tests/test_fura_cli_standalone.py -q` (`cli-suite`) |

## Guardrails

- CLI imports remain cheap and command modules own their flags and handlers without eager-loading unrelated capabilities.
- Human and machine output preserve stdout/stderr separation, stable fields, actionable recovery, and no secret leakage.

## Edges

- dispatches-to → **cli_commands** (subcommands)
- exposed-by → **package** (console entry point)

## Owns

- **code:** `src/furatena/cli/*.py`
- **tests:** `tests/test_cli_entrypoint_smoke.py`, `tests/test_fura_cli_standalone.py`
- **docs:** `README.md`, `docs/CLI_CONTRACT.md`
