<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: cli_commands

Keep each Furatena command thin over catalog services while preserving command-specific validation, side effects, and recovery text.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Command modules remain lazily dispatched with stable parser and handler contracts. | P1 | machine-backed | `uv run pytest tests/test_cli_command_modules.py -q` (`cli-commands-suite`) |

## Guardrails

- Commands do not duplicate catalog policy; they translate arguments, call one owning service, and present results.
- Mutating, server, export, migration, publication, and author commands make side effects and trust posture explicit.

## Edges

- registered-by → **cli** (lazy dispatcher)
- invokes → **catalog** (domain services)

## Owns

- **code:** `src/furatena/cli/commands/`
- **tests:** `tests/test_cli_command_modules.py`
- **docs:** `docs/CLI_CONTRACT.md`, `content/furatena/docs/reference/generated-cli-config.md`
