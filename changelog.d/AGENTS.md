<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: changelog

Keep unreleased Towncrier fragments accurate, user-centered, correctly categorized, and connected to the behavior they announce.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Configured Towncrier fragments produce a valid unreleased draft. | P1 | machine-backed | `make changelog-draft` (`changelog-draft`) |

## Guardrails

- Use `ISSUE.TYPE.md` and configured added, changed, deprecated, removed, fixed, or security categories.
- Fragments are complete sentences that name user or operator impact; they do not substitute for docs or migration notes.

## Edges

- released-with → **package** (distribution metadata)
- summarizes → **docs** (public behavior changes)

## Owns

- **code:** `changelog.d/`
- **tests:** `scripts/check_changelog_fragments.py`
- **docs:** `CHANGELOG.md`, `changelog.d/README.md`
