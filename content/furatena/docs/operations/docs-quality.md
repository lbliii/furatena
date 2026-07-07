---
title: Gate documentation completeness
description: Detect orphan pages, navigation gaps, broken links, and undocumented public features in CI.
weight: 45
lang: en
type: doc
category: operations
tags: [documentation, quality, ci, links, navigation, coverage]
owner: docs-product
reviewed_at: "2026-07-07"
---

# Gate documentation completeness

Run the unified documentation gate before publishing:

```bash
uv run fura docs-quality --json
```

The command indexes the live catalog and implementation-owned public surfaces,
statically validates shell fences, and checks review ownership:

| Rule id | Detects | Recommended response |
|---|---|---|
| `fura.docs_quality.orphan` | A public page with no inbound content or navigation link | Link it from the owning tutorial/how-to journey. |
| `fura.docs_quality.navigation` | A default-mount public page omitted from generated navigation | Add it to navigation or explain why it is represented by another entry point. |
| `fura.docs_quality.link` | An internal content link that does not resolve | Repair the source link or restore the target. |
| `fura.docs_quality.public_feature` | Missing or stale coverage for a CLI, route, config, MCP, sidecar, diagnostic, or deployment contract | Add or refresh the recommended reference page. |
| `fura.docs_quality.snippet` | Invalid shell syntax, unknown `fura` option/command, or unknown Make target | Fix or retag the snippet; commands are parsed but never executed. |
| `fura.docs_quality.freshness` | Missing owner/review date or a review older than the threshold | Assign an owner, review the page, and update `reviewed_at`. |
| `fura.docs_quality.exemption` | An exemption that no longer matches a finding | Remove or update the stale exemption. |
| `fura.docs_quality.*` | Dynamic docs-quality rule family used by inventory tooling | Resolve the concrete rule emitted in the diagnostic. |

Each diagnostic names the owning workstream and recommends a Diataxis page type
(`tutorial`, `how-to`, `explanation`, or `reference`) in `next_action`. Active
findings and unused exemptions return validation exit code 2.

Every page under Operations and Reference must declare `owner` and an ISO
`reviewed_at: "YYYY-MM-DD"` value. The default maximum age is 180 days; use
`--freshness-days N` to apply an organization-specific threshold. Shell fences
tagged `bash`, `sh`, `shell`, or `console` receive non-executing Bash syntax
validation. Any `fura` commands and Make targets inside them are also checked
against the active parser and Makefile.

## Reasoned exemptions

The default file is `docs/docs-quality-exemptions.json`. An exemption must name
the exact finding id, choose `internal` or `deferred`, and give a non-empty
reason:

```json
{
  "schema_version": 1,
  "exemptions": [
    {
      "finding": "navigation:/compatibility-entry/",
      "disposition": "internal",
      "reason": "The canonical page already occupies this navigation position."
    }
  ]
}
```

Use `internal` only when documenting the implementation detail would mislead a
user. Use `deferred` when a named, scheduled work item owns the missing page.
The gate fails unused exemptions so the file cannot silently accumulate stale
waivers.

## CI contract

`make ci-contract` runs the gate with `PYTHON_GIL=0` on CPython 3.14t. The JSON
payload includes active findings, applied exemptions with the original finding
detail, unused exemptions, and the implementation-derived inventory summary.
Do not suppress broken links or undocumented user-facing features merely to make
the lane pass; repair them or record a reviewable product decision.
