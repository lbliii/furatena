# Authoring Lifecycle

Furatena treats source front matter as the contract for local drafting, private preview, and publication safety. `fura check` validates lifecycle fields directly from source files, including drafts that are not indexed as public catalog nodes.

## Fields

- `draft`: boolean. Draft pages are private author-mode work and are excluded from public catalog output.
- `visibility`: one of `public`, `private`, `internal`, `draft`, `unlisted`, or `archived`.
- `published_at`: ISO date or datetime for public lifecycle-managed pages.
- `updated_at`: ISO date or datetime for the latest source update.
- `owner`: person, team, or service responsible for the page.
- `reviewers`: string or list of reviewers for publication.
- `expires_at`: optional ISO date or datetime for time-bound content.
- `archived_at`: optional ISO date or datetime for archived content.

## States

- Draft: `draft: true` or `visibility: draft`.
- Private preview: `visibility: private` or `visibility: internal`.
- Unlisted: `visibility: unlisted`, available to explicit author/private reads but excluded from public indexes.
- Public: `visibility: public` with `published_at`.
- Archived: `visibility: archived` or `archived_at`.

Pages with no lifecycle fields remain public by legacy default. To opt into lifecycle enforcement for public pages, set `visibility: public` and `published_at`.

## Safety Checks

`fura check` reports lifecycle errors when:

- `visibility` is outside the allowed set.
- Date fields are not ISO dates or datetimes.
- Draft pages set `visibility: public`.
- Draft, private, internal, unlisted, or archived pages set `published_at`.
- Archived pages set `visibility: public`.
- Public source links to a draft, private, internal, or archived target.
- `reviewers` is neither a string nor a list.

`fura check` reports warnings when:

- Lifecycle-managed public pages omit `published_at`.
- `owner` is present but empty.

These checks run before static export and before agent workflows rely on catalog sidecars, so draft/private content remains blocked from public publishing by default.

`fura export` fails when lifecycle errors are present. Use `--allow-lifecycle-errors` only for local debugging or intentionally unsafe previews; publishing workflows should not set it.

Author-mode JSON routes such as `/catalog.json`, `/search.json`, `/catalog/retrieve`, and `/llms.txt` exclude non-public pages by default. Add `include_private=1` only for trusted local authoring tools.

## CLI Lifecycle Commands

`fura author` exposes deterministic local source operations:

```bash
fura author new docs/new-page --title "New page" --dry-run --json
fura author new docs/new-page --title "New page" --yes --json
fura author status docs/new-page --json
fura author validate docs/new-page --json
fura author edit docs/new-page --old-text "Draft" --new-text "Reviewed draft" --dry-run --json
fura author edit docs/new-page --old-text "Draft" --new-text "Reviewed draft" --yes --json
fura author draft docs/new-page --dry-run --json
fura author publish docs/new-page --yes --json
fura author unpublish docs/new-page --yes --json
fura author archive docs/new-page --yes --json
```

Mutating commands require either `--dry-run` or `--yes`. `fura author validate` is read-only and reports target-scoped content and lifecycle diagnostics with validation exit code `2` on failure. `fura author edit` applies an exact source span replacement, so agents should run `status` or `author_read_source` first and pass the precise `--old-text` value they intend to replace. JSON responses include an operation id, target path, mount id, previous and resulting visibility, changed files, diagnostics, diff preview, and next actions. Lifecycle transitions also include `publication_impact`, which reports whether public output inclusion changes and whether navigation, search, export, and agent retrieval surfaces are affected before any write occurs.

## MCP Authoring Tools

Local agents can use `fura mcp --author --include-private` for source-aware authoring. Authoring tools are disabled unless the MCP session explicitly includes private content.

- `author_create_draft` creates a draft source file and defaults to dry-run.
- `author_read_source` reads source for review and edit planning.
- `author_propose_edit` returns an exact-text edit diff without writing.
- `author_apply_edit` writes an exact-text edit only when `confirmed: true` and `dry_run: false`.
- `author_validate` returns structured validation diagnostics.
- `author_publish` and `author_unpublish` change lifecycle state and default to dry-run.
- `author_inspect_publication_impact` reports lifecycle state, validation, and stale impact.

Every authoring tool response includes audit metadata with actor, command, target path, previous state, resulting state, diagnostics, dry-run state, and confirmation state.
