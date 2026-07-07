---
title: Migrate from JS docs
owner: devrel
reviewed_at: "2026-07-07"
description: Move MDX-heavy docs from Mintlify, Fern, Docusaurus, or custom React stacks into Furatena.
weight: 35
lang: en
type: doc
tags: [migration, mdx, mintlify, fern, docusaurus]
category: operations
---

# Migrate from JS docs

Furatena's migration wedge is simple: keep the docs corpus, remove the build-time React
surface, and lower MDX components into catalog-native directives where possible.

## First pass

Run a dry migration against a standalone app or the current repository:

```bash
fura migrate --dry-run
```

Then migrate once the report looks right:

```bash
fura migrate
```

For a read-only migration readiness report across mounted sources:

```bash
fura migrate --report
fura migrate --report --json
```

The report groups check and compatibility findings by severity, source path,
construct, owner, and suggested action. Its remediation plan additionally groups
every item by ecosystem and risk (`safe`, `review`, `manual`, or `blocking`). It
includes broken internal or cross-mount links, unresolved reference roles,
directive compatibility gaps, and embedded MDX/RST/MyST constructs that need
mapping or manual cleanup.

## Safe automated remediation

Preview only deterministic, reversible MDX conversions:

```bash
fura migrate --apply-safe --dry-run --json
```

Apply the approved safe set:

```bash
fura migrate --apply-safe --json
```

Safe mode creates a canonical `.md` sibling only when the converted document is
parse-clean, contains no unmapped JSX component, and has no conflicting target.
It never deletes the `.mdx` source and never overwrites an existing `.md` file.
Deleting the generated sibling is therefore the complete rollback. Repeating the
command is idempotent; a matching sibling is reported as `unchanged`.

Anything ambiguous remains a manual blocker under
`fura.migration.remediation.manual`. The structured result names its source and
reason, while `fura migrate --report --json` supplies the owner, ecosystem, risk,
and recommended action for backlog routing.

By default, `.mdx` files become `.md` siblings and the source `.mdx` file is removed.
Use `--keep-mdx` while evaluating:

```bash
fura migrate --keep-mdx
```

## What gets converted

| MDX shape | Furatena output |
|-----------|-----------------|
| Capitalized JSX block | Lowercase Patitas directive block |
| JSX attributes | Directive options |
| Self-closing JSX component | Empty directive block |
| Markdown content | Preserved as markdown |

Unmapped JSX components are reported as warnings so you can choose whether to add a
directive handler, rewrite the content, or keep the page as HTML.

## Compatibility diagnostics

`fura check` reports format compatibility gaps before migration:

| Source construct | Behavior |
|------------------|----------|
| MDX JSX component matching a Furatena directive | Recorded as mapped compatibility metadata |
| MDX JSX component without a directive mapping | Warning with source line and migration action |
| RST admonition directive | Recorded as mapped Content IR directive metadata |
| RST directive without a Furatena directive contract | Warning with source line and adapter behavior |
| RST role such as `:py:class:` | Warning that the target is not resolved through Furatena inventories |
| MyST directive fence such as ```` ```{note}```` | Lowered to a Patitas directive when registered |
| MyST ref/doc roles | Lowered to markdown links and included in Content IR links |
| Unsupported MyST roles or directives | Warning with source line and adapter behavior |

Use these warnings as the first triage list for custom directive mappings or manual
cleanup. They are intentionally conservative: rendered HTML can still work while the
diagnostic tells you which constructs are not yet native Furatena contracts.

## Platform notes

| Source | Migration path |
|--------|----------------|
| Mintlify | Import the docs tree, convert MDX components, move navigation into `mounts.yaml` and front matter weights |
| Fern | Keep API/reference content as markdown or generated autodoc, then expose `/catalog.json` and `/tools.json` for agents |
| Docusaurus | Migrate docs/blog MDX first, then replace sidebars with Furatena catalog sections |

## Validate after migration

```bash
fura check --content-only --warnings-as-errors
fura query --directive cards
fura serve --author
```

The goal is not one-shot magic. The goal is to make the first conversion mechanical,
surface the small set of JSX components that still need decisions, and immediately get a
live docs app with static and agent exports from the same corpus.

## Mixed MDX, MyST, and RST workflow

Do not bulk-rewrite the entire corpus first. Mount the original extensions, produce
the read-only readiness report, and group findings by construct and owner:

```bash
fura migrate --report --json
fura check --content-only --json
```

Then handle each format deliberately:

1. **MDX:** run `fura migrate --apply-safe PATH --dry-run --json`, review mapped
   directive options and `fura.migrate.unmigrated_component` warnings, then rerun
   without `--dry-run` only for the safe files. Use the legacy bulk conversion
   only when a reviewed migration commit intentionally replaces its MDX sources.
2. **MyST:** keep registered directive fences and supported ref/doc roles; repair
   unsupported roles from the compatibility report before changing extensions.
3. **RST:** preserve supported admonitions and inventory-backed references; assign
   unresolved roles/directives to a manual mapping or source rewrite.
4. Rerun the report after each batch. A falling warning count is migration progress;
   hiding warnings with a renderer-only fallback is not.

Use source control for the rollback boundary. Keep generated `.md` siblings in a
reviewable commit separate from later navigation or visual changes.

## OpenAPI workflow

Keep OpenAPI as a generated source, not hand-copied prose. Add it to
`config/autodoc.yaml`:

```yaml
autodoc:
  python:
    enabled: false
  openapi:
    enabled: true
    output_prefix: api/rest
    display_name: REST API
    specs:
      - specs/openapi.yaml
```

Run:

```bash
fura check --content-only --warnings-as-errors
fura api-diff previous-openapi.yaml specs/openapi.yaml --json
fura freeze
fura export
```

The check blocks invalid specs, missing operation metadata, broken examples,
unresolved schema references, and unsafe try-it contracts. Verify generated
operations in `/catalog/api-operations.json`, `/tools.json`, search, and the MCP
`list_api_operations` tool. Keep authenticated live proxy tokens server-only;
static output must retain mock/static fallbacks and never publish credentials.

## Completion checklist

- No blocking migration or content diagnostics remain.
- Every unsupported construct has an owner and explicit manual/deferred decision.
- Every automated rewrite is parse-clean, source-preserving, conflict-free, and reversible.
- Internal and cross-mount links resolve after the final file moves.
- OpenAPI operations have stable ids, summaries, responses, and valid examples.
- Freeze/export and public agent outputs contain the migrated content once, at the
  intended visibility.
