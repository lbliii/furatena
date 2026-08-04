# Author Mutation Threat Model

This document defines the security boundary for Furatena operations that read or
change author source. It covers the browser author surface, the `fura author`
CLI, and MCP authoring tools. It records the controls that exist today, the
controls required before a surface can be exposed beyond a trusted local
machine, and the issues that own unresolved risk.

The default deployment remains local authoring on `127.0.0.1`. Until the
blocking browser issues in this document are closed, author mode must not be
bound to a non-loopback interface or placed behind a public reverse proxy.

## Security objectives

Authoring must preserve these properties:

1. Only an authorized subject can read private source or request a mutation.
2. A mutation is never triggered by navigation, prefetching, crawling, or a
   cross-site request.
3. Preview and dry-run operations never write source.
4. A caller sees the exact target, diff, lifecycle impact, and confirmation
   boundary before a write.
5. Source paths stay inside the selected content mount.
6. A write cannot silently replace a newer source revision.
7. Audit identity comes from the trusted session or transport, not an
   untrusted request field.
8. Draft, private, internal, unlisted, and archived content cannot enter public
   browser, export, search, or agent output accidentally.
9. A failed or partial operation leaves recoverable source and an actionable
   diagnostic.

## Assets and trust zones

| Asset or zone | Security concern | Trust assumption |
|---------------|------------------|------------------|
| Content roots and front matter | Unauthorized read, overwrite, traversal, lifecycle escalation | The operating-system user owns the checkout |
| Git working tree | Recovery, attribution, concurrent edits | Git is the durable rollback mechanism; Furatena does not commit automatically |
| Browser public surface | Cross-site requests, anonymous access, htmx response confusion | Public routes are untrusted input |
| Browser author surface | Source disclosure and mutation | Trusted only when loopback-bound and running in author mode; session authorization is still required before remote use |
| Local CLI | Accidental mutation and ambiguous targets | The local shell user is the actor |
| Local MCP stdio | Tool misuse and private-source disclosure | The launching process controls the stdio peer |
| Remote MCP policy | Token theft, actor spoofing, rate abuse, cross-tenant access | The deployment supplies trusted identity, token storage, and transport security |
| Frozen and static output | Private-content disclosure | Export filtering is a security boundary, not just a presentation choice |

## Actors

- **Anonymous browser visitor**: may read only public content and must never
  reach author source or mutation handlers.
- **Reader**: may read explicitly permitted non-public pages, but cannot author.
- **Contributor**: may read drafts and edit source within allowed mounts.
- **Publisher**: may perform contributor actions and publish or unpublish.
- **Administrator**: may archive, configure, and operate protected surfaces.
- **Local shell user**: supplies operating-system authority for CLI writes.
- **Local MCP client**: inherits the authority of the process that launched the
  author MCP session.
- **Remote MCP client**: is untrusted until the deployment authenticates it and
  maps trusted claims into a Furatena access subject.
- **Cross-site attacker or crawler**: can cause browser navigation and form
  submissions but must not be able to mutate source.
- **Concurrent author or process**: can change a file after an editor loaded it
  and before that editor saves.

## Mutation inventory

| Surface | Entry point | Write operations | Current identity boundary | Confirmation / CSRF | Audit and rollback | Required follow-up |
|---------|-------------|------------------|---------------------------|---------------------|--------------------|--------------------|
| Browser author chrome | `POST /docs/_author/transition` | `draft`, `publish`, `unpublish`, `archive` | Server-owned subject from a signed session; per-node role policy | Session-backed CSRF token, exact source revision, and explicit confirmation for writes | Operation id and diff response; Git/manual rollback | Remote author mode still requires a trusted gateway boundary |
| Browser author studio | `POST /docs/_author/studio/save` | Create draft, replace full source | Server-owned subject from a signed session; per-node role policy | Session-backed CSRF token, exact source revision, and explicit write intent | Operation result and reindex; Git/manual rollback | Remote author mode still requires a trusted gateway boundary |
| Local CLI | `fura author new` | Create draft source | Local OS user | `--dry-run` or `--yes` | JSON operation id, diff, changed files; Git/manual rollback | Authorization matrix in #151 documents the local trust exception |
| Local CLI | `fura author edit` | Exact-span replacement | Local OS user | `--dry-run` or `--yes` | Unique old-text match, diff, operation id; Git/manual rollback | No browser CSRF requirement |
| Local CLI | `fura author draft/publish/unpublish/archive` | Lifecycle front-matter change | Local OS user | `--dry-run` or `--yes` | Publication impact, diff, operation id; Git/manual rollback | Publisher/admin policy remains required for non-local wrappers |
| MCP authoring | `author_create_draft`, `author_apply_edit` | Create or edit source | `include_private` author session; remote sensitive tools also require a privileged token | `dry_run` defaults true; `confirmed=true` required to write | Sanitized in-memory audit, diff, operation id; Git/manual rollback | #189, #191, #192 before production remote operation |
| MCP lifecycle | `author_publish`, `author_unpublish`, `author_archive` | Lifecycle front-matter change | Same as MCP authoring | `dry_run` defaults true; `confirmed=true` required to write | Sanitized in-memory audit and publication impact | #189, #191, #192 before production remote operation |

Sensitive read-only operations are part of the same boundary. Browser source
reads, `author_read_source`, validation, proposed edits, and publication-impact
inspection can disclose private source even though they do not write it.

## Existing controls

### Source targeting

- New files are resolved below a selected mount and rejected when the resolved
  path escapes that mount.
- Existing targets must resolve to a unique file in an allowed mount.
- Exact-text edits require one unique match, which reduces accidental broad
  replacements.
- Empty source and invalid front matter are rejected by whole-source saves.

These controls prevent simple `..` traversal and ambiguous-target writes. Tests
for the role and lifecycle matrix must keep path containment independent from
authorization: a safe path is not proof that the caller may mutate it.

### Intent and preview

- CLI and MCP writes require an explicit confirmation and support dry-run.
- MCP mutation schemas default to dry-run.
- Lifecycle transitions report public-output impact before a write.
- Operation results include identifiers, changed files, diagnostics, and diffs.

Browser lifecycle links do not currently meet this standard because they encode
confirmation in a state-changing GET URL.

### MCP transport policy

- Author tools require an author session with private content enabled.
- Remote sensitive tools require a configured privileged token.
- Token-shaped inputs are redacted from audit payloads.
- A per-process rate limit and output bound are applied.

These are local/runtime safeguards, not a complete deployed identity system.
Remote production use remains blocked on trusted claim mapping (#189), durable
audit (#191), and restart-safe shared limits (#192).

### Public-output isolation

Lifecycle and RBAC policies filter public search, DCP, static, PDF, `llms.txt`,
sidecar, and MCP output. Canary leak coverage is tracked by #158.

## Abuse cases and disposition

| Threat | Impact | Current mitigation | Disposition |
|--------|--------|--------------------|-------------|
| Cross-site link, prefetch, or crawler triggers lifecycle GET | Critical integrity and publication change | Confirmation query flag only | **Blocking:** #100 converts transitions to POST with session-backed CSRF |
| Cross-site form submits an author-studio save | Critical source overwrite | Author mode check only | **Blocking:** #100 applies real CSRF to every browser mutation |
| Author routes are exposed off-loopback without an authenticated role | Critical source disclosure and mutation | Default bind is `127.0.0.1`; author-mode flag | **Blocking for remote author mode:** #151 and #189 |
| A stale editor overwrites a newer whole-source revision | High integrity loss | Git can recover after the fact | **Blocking:** #197 adds a source-version precondition |
| Request-supplied MCP actor spoofs audit attribution | High audit-integrity loss for remote use | Policy actor exists, but request actor is currently accepted | **Blocking for remote MCP:** #189; trusted transport identity must win |
| Malicious slug or explicit path escapes a mount | Critical arbitrary file write | Resolved-path containment and mount matching | Mitigated; preserve in #151 tests |
| Caller omits write confirmation | High accidental mutation | CLI `--yes`; MCP `confirmed`; dry-run defaults | Mitigated; preserve in #151 tests |
| Private content appears in public artifacts | Critical confidentiality loss | Lifecycle and RBAC filtering | Validate continuously in #158 |
| Privileged token appears in audit output | High credential disclosure | Token-shaped keys are redacted | Mitigated; retain regression tests |
| Process restart loses MCP audit or resets rate state | Medium operational and abuse risk | In-memory audit and counters | #191 and #192 before production remote MCP |
| Partial filesystem or reindex failure follows a write | Medium availability/integrity risk | Diagnostics and Git rollback | Document recovery now; durable reconciliation belongs to #195 and #196 |

## Required browser contract

Issues #100 and #151 must leave the browser author surface with this contract:

1. Every mutation uses POST. GET may render a preview or confirmation form but
   cannot change source or lifecycle state.
2. A server-generated session contains the authenticated subject and CSRF
   secret. Request fields cannot choose actor, roles, teams, tenant, or site.
3. Browser mutations require a validated CSRF token and the permission implied
   by the operation (`author`, `publish`, or `administer`).
4. htmx and non-htmx submissions enforce identical security decisions.
5. Failures return explicit status, diagnostics, and a non-mutating response.
6. Author mode bound beyond loopback fails closed unless a trusted identity
   integration is configured.

## Required concurrency contract

Whole-source browser and agent saves must carry a source revision derived from
the content that was read, such as a strong content hash. The write must compare
that revision immediately before replacement and return a conflict without
writing when the source changed. The response should include the current
revision and instruct the caller to reread, merge, and retry.

Exact-text edits still benefit from the same precondition. A unique span match
prevents some stale edits but does not prove that unrelated source changes are
safe to overwrite or publish.

## Verification checklist

- GET requests cannot change files or lifecycle state.
- POST without a session or with an invalid CSRF token fails.
- Anonymous and reader subjects cannot read author source.
- Contributor, publisher, and admin permissions match the matrix in #151.
- A stale source revision returns a conflict and leaves the file unchanged.
- `..`, symlink, ambiguous mount, and out-of-root targets fail.
- Dry-run leaves source and catalog indexes unchanged.
- Audit records use trusted actor identity and redact secrets.
- Gateway and SSO claims follow the fail-closed mapping and signed-session
  boundary in [Trusted gateway and SSO identity](TRUSTED_GATEWAY_IDENTITY.md).
- Private canary content is absent from every public output.
- Recovery guidance covers Git restore, reindex, freeze, and export refresh.

## Issue ownership

- #100: POST-only browser mutations, session-backed CSRF, explicit failures.
- #151: role-by-lifecycle and cross-transport authorization matrix.
- #158: public artifact visibility leak scanner.
- #197: stale-write prevention with source-version preconditions.
- #189: trusted gateway/SSO claim mapping and actor attribution.
- #191: durable audit storage and redaction policy.
- #192: restart-safe shared rate limiting.
- #195 and #196: source-sync and deployment failure recovery.

The stale-write precondition in #197 is a separate P0 child of the
secure-mutation epic because neither CSRF nor authorization prevents an
authorized but stale editor from destroying a newer revision.
