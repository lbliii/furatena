---
title: Public surface inventory
owner: platform-docs
reviewed_at: "2026-07-07"
description: Machine-readable documentation coverage for product contracts.
weight: 45
---

# Public surface inventory

`fura docs-inventory` derives the public product surface from implementation
metadata and links each entry to documentation that names its stable identifier.
The committed report is [`docs/public-surface-inventory.json`](https://github.com/lbliii/furatena/blob/main/docs/public-surface-inventory.json).

The inventory covers:

- every `fura` CLI command and nested command;
- live routes and machine-readable sidecars such as `/catalog.json`,
  `/semantic.json`, `/surface.json`, and `/routes.json`;
- `docs.yaml` and `mounts.yaml` configuration fields;
- MCP tools and stable MCP resources;
- diagnostic rule identifiers; and
- supported deployment profiles.

Generate or refresh the report from the repository root:

```bash
fura --app-root app docs-inventory \
  --output docs/public-surface-inventory.json
```

The output has a stable `schema_version`, per-kind summary counts, explicit
`missing` and `stale` id lists, and one record per surface. Each record includes
its implementation source, an implementation fingerprint, linked documentation,
a documentation fingerprint, and `documented`, `missing`, or `stale` coverage.

When an existing output file is refreshed, it is also the default baseline. A
surface becomes stale when its implementation contract changes but its linked
documentation fingerprint does not. Pass `--baseline PATH` to compare against a
different prior report, or repeat `--docs-root PATH` to inventory another docs
tree.

Missing entries are planning input, not hidden exclusions. Add documentation
that names the stable command, route, field, URI, rule id, sidecar URL, or profile
id; then regenerate the report so the link is evidence-based.
