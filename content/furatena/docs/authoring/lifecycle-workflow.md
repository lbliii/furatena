---
title: Review and publish an author change
description: Use Author Studio, validation, lifecycle transitions, and public-output inspection safely.
weight: 45
lang: en
type: doc
tags: [author-studio, lifecycle, validation, publish, public-output]
category: authoring
---

# Review and publish an author change

This workflow moves one page from an author-only draft to verified public output.
It uses the browser studio for editing and the CLI for reviewable state changes.

## 1. Start the trusted author surface

```bash
export PYTHON_GIL=0
uv run fura serve --author
```

Open `/docs/_author/dashboard` to review mount health and validation counts. Open an
existing page in Author Studio with
`/docs/_author/studio?slug=docs/get-started`, or create a draft with
`/docs/_author/studio?new=1&slug=docs/proposed-page&title=Proposed%20page`.

Author Studio saves are POST-only, require the signed session/CSRF proof rendered
by the app, and reindex the changed source. Keep author mode loopback-only unless
an authenticated role and deployment boundary protect it.

## 2. Inspect status and validate

On the page itself, use **State and actions** as the truthful sequence. Its
Lifecycle, Repository, Artifact, and Deployment cards are independent. An
unavailable repository, artifact, or deployment card means that service has not
provided an exact record; public lifecycle state alone does not mean the change
is merged, built, promoted, or healthy. Expand **Exact identities and source
tools** to compare the source revision, plan digest, artifact digest, and
deployment identity before taking the next action.

```bash
uv run fura author status docs/proposed-page --json
uv run fura author validate docs/proposed-page --json
```

`status` returns the source revision, current visibility, export impact, and next
actions. `validate` returns target-scoped diagnostics. Fix validation errors before
requesting a lifecycle transition; do not use publication state to hide broken
content.

## 3. Preview the lifecycle change

```bash
uv run fura author publish docs/proposed-page \
  --source-revision sha256:REVISION \
  --dry-run --json
```

The dry run reports the previous/resulting state, exact source diff, changed files,
diagnostics, and `publication_impact` for navigation, search, static export, and
agent retrieval. If another writer changed the source, refresh `status` and review
again rather than bypassing the `fura.author.conflict` revision check.

In the browser, **Review lifecycle diff** performs the same non-mutating preview.
**Inspect public output** is also read-only and is available only when the server
authorizes publication-impact inspection. Neither action writes source or reindexes
the catalog.

## 4. Confirm, then revalidate

```bash
uv run fura author publish docs/proposed-page \
  --source-revision sha256:REVISION \
  --yes --json
uv run fura author validate docs/proposed-page --json
```

Use `draft`, `unpublish`, or `archive` with the same dry-run/revision/confirmation
sequence. Browser lifecycle controls perform the same policy check and POST to
`/docs/_author/transition`; CLI and MCP clients must never emulate that browser
form without its session and CSRF boundary.

The browser separates opening **Mark public** or **Mark draft** from the final
confirmation button. That native disclosure works with JavaScript disabled. The
confirmed effect changes source lifecycle metadata only: it does not create a Git
review, build an artifact, promote, verify serving, or roll back. Those steps stay
separate and remain disabled until connected automation returns exact identities and
server-owned capabilities.

## 5. Inspect every public projection

```bash
uv run fura export --fresh --base-path ""
uv run fura query --json --url-prefix /docs/proposed-page
```

Confirm the page appears in:

- its HTML route and page-level `index.txt`;
- `app/public/catalog.json` and `app/public/search.json`;
- `app/public/llms.txt` and `app/public/llms-full.txt`;
- `app/public/tools.json` when it changes an agent-facing operation; and
- `app/public/channels.json` as part of the static/agent outputs.

For an unpublish/archive review, assert the inverse: the title, slug, and a unique
content canary must be absent from every public file while remaining available to
an authorized `--include-private` author session.

## 6. Recover safely

- Validation failure: repair source, rerun `author validate`, and repeat the dry run.
- Revision conflict: rerun `author status`; never reuse a stale revision.
- Wrong publication impact: stop before `--yes` and correct front matter/navigation.
- Bad public artifact: unpublish with a confirmed transition, rebuild, and inspect
  the outputs again before retrying publish.

For agent-driven changes, follow the same sequence with `author_read_source`,
`author_propose_edit`, `author_apply_edit`, `author_validate`, and
`author_inspect_publication_impact`; mutating MCP tools stay dry-run by default.
