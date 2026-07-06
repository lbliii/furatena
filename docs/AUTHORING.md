# Authoring Lifecycle

Furatena treats source front matter as the contract for local drafting, private preview, and publication safety. `fura check` validates lifecycle fields directly from source files, including drafts that are not indexed as public catalog nodes.

Author mutation trust boundaries, confirmation requirements, unresolved risks,
and the remote-exposure policy are defined in
[AUTHOR_MUTATION_THREAT_MODEL.md](AUTHOR_MUTATION_THREAT_MODEL.md).

## Fields

- `draft`: boolean. Draft pages are private author-mode work and are excluded from public catalog output.
- `visibility`: one of `public`, `private`, `internal`, `draft`, `unlisted`, or `archived`.
- `published_at`: ISO date or datetime for public lifecycle-managed pages.
- `updated_at`: ISO date or datetime for the latest source update.
- `owner`: person, team, or service responsible for the page.
- `reviewers`: string or list of reviewers for publication.
- `expires_at`: optional ISO date or datetime for time-bound content.
- `archived_at`: optional ISO date or datetime for archived content.
- `access`: optional RBAC policy for roles, teams, or admin-only pages. See
  [RBAC.md](RBAC.md).

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
- A public source page has no frozen HTML page or changed after its frozen page was written.

These checks run before static export and before agent workflows rely on catalog sidecars, so draft/private content remains blocked from public publishing by default.

Pages with `access.roles`, `access.teams`, or `access.admin_only` are treated as
non-public for default public outputs even when their lifecycle visibility is
`public`. This prevents team and admin-only pages from leaking into search,
frozen exports, or agent retrieval while the broader permission-aware output
filters are applied.

`fura check --deploy` treats stale public output as an error. Refresh the public output with `fura freeze` or `fura export --fresh` before deploying.

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

Mutating commands require either `--dry-run` or `--yes`. `fura author validate` is read-only and reports target-scoped content and lifecycle diagnostics with validation exit code `2` on failure. `fura author edit` applies an exact source span replacement, so agents should run `status` or `author_read_source` first and pass the precise `--old-text` value they intend to replace. JSON responses include an operation id, target path, mount id, previous and resulting visibility, changed files, diagnostics, diff preview, and next actions. Lifecycle transitions also include `publication_impact`, which reports whether public output inclusion changes and whether navigation, search, export, and agent retrieval surfaces are affected before any write occurs. Transitions clear incompatible lifecycle timestamps when changing states, such as removing `archived_at` before publishing or drafting an archived page.

## Browser Mutation Security

Browser authoring uses signed Chirp sessions and session-backed CSRF tokens.
Lifecycle changes and studio saves are POST-only. The author forms render a
hidden CSRF field for ordinary browser submission, while the shell copies the
same token into the `X-CSRF-Token` header for htmx requests. A missing or
invalid token returns `403` without changing source.

Authorization is evaluated after CSRF validation and before source access. A
contributor can create, edit, and draft; a publisher can also publish and
unpublish; only an admin can archive. Browser identity is read from the signed
session, and submitted form fields cannot select an actor or role. See
[RBAC.md](RBAC.md#author-mutation-matrix) for the shared browser, CLI, and MCP
matrix.

## Source Revision Preconditions

Source reads and status responses return a strong `sha256:<hex>`
`source_revision`. Every write to an existing source file must send that value:
the browser studio and lifecycle forms do this automatically, CLI edit and
lifecycle commands use `--source-revision`, and MCP edit/lifecycle tools use
`source_revision` from `author_read_source`.

Furatena rereads the file immediately before replacement. A missing or stale
revision returns `fura.author.conflict`, includes the current revision, and
leaves the newer source unchanged. Reread the source, merge both the intended
span and any unrelated concurrent edits, then retry with the current revision.
Dry runs remain non-mutating and return the revision to use for a later write.

Successful lifecycle forms use Chirp `FormAction` semantics: htmx receives the
updated author-chrome fragment, and a browser without JavaScript receives a
`303` redirect to the affected page. GET requests to the transition endpoint
return `405` and cannot mutate lifecycle state.

Local development uses an ephemeral signing secret, so author sessions reset
when the process restarts. Set `FURA_SESSION_SECRET` (or
`CHIRP_SECRET_KEY`) to a stable random value for staging or production and set
`FURA_ENV=staging` or `FURA_ENV=production` to enable the corresponding Chirp
cookie-security posture. Furatena refuses a staging or production app without a
configured secret.

## MCP Authoring Tools

Local agents can use `fura mcp --author --include-private` for source-aware authoring. Authoring tools are disabled unless the MCP session explicitly includes private content.

- `author_create_draft` creates a draft source file and defaults to dry-run.
- `author_read_source` reads source for review and edit planning.
- `author_propose_edit` returns an exact-text edit diff without writing.
- `author_apply_edit` writes an exact-text edit only when `confirmed: true` and `dry_run: false`.
- `author_validate` returns structured validation diagnostics.
- `author_publish`, `author_unpublish`, and `author_archive` change lifecycle state and default to dry-run.
- `author_inspect_publication_impact` reports lifecycle state, validation, and stale impact.

Every authoring tool response includes audit metadata with actor, command, target path, previous state, resulting state, diagnostics, dry-run state, and confirmation state.
