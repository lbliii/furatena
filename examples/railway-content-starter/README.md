# Furatena Railway content starter

This repository contains adopter-owned public content and presentation
configuration for the proprietary Furatena Railway template. It does not
contain the Furatena application or require a Python package.

1. Create a public repository from this directory.
2. Edit Markdown below `app/content/` and commit it.
3. Set the Railway template's `FURA_CONTENT_REPOSITORY` to the HTTPS clone URL.
4. Pushes can call the authenticated refresh endpoint by configuring the
   `FURATENA_REFRESH_URL` and `FURATENA_REFRESH_TOKEN` repository secrets.

## Publish content

The included workflow reads the active commit, then submits a versioned refresh
request with that expected value, the exact pushed commit, and a GitHub-run
idempotency key. Furatena proves the commit is reachable from the configured
`main` policy, validates and freezes a complete generation, and switches
generations atomically without rebuilding the application image.

HTTP 202 returns an operation/status URL. Treat `stale_active_commit`,
`unreachable_commit`, `idempotency_key_collision`, and `refresh_in_progress` as
409 conflicts requiring a fresh operator decision. Empty-body POST is v1-only
migration compatibility and does not provide exact-commit or caller-controlled
idempotency guarantees. Webhook delivery is deferred beyond v1.

The `main` ref is the normal content channel. Maintainers of the conformance
fixture also keep a `conformance-v2` tag so the template harness can prove
update and rollback behavior.
