# Furatena Railway content starter

This repository is the adopter-owned half of a Furatena Railway deployment. It
contains public documentation and safe site configuration, but no Furatena
application source or Python package. Runtime releases and documentation
changes have separate identities and rollback paths.

## Create your site

1. Create a public repository from this directory.
2. Replace the fictional Acme pages below `app/content/` with your product
   documentation. Keep authored assets beside the pages that use them.
3. Set the Railway template's `FURA_CONTENT_REPOSITORY` to the repository's
   credential-free HTTPS clone URL.
4. Leave `FURA_CONTENT_REF=main` unless your team deliberately publishes from
   another branch or exact commit.
5. Deploy, then confirm the expected content commit and image digest in
   `/meta.json`.

The content repository must remain public in the v1 template. Never put a Git
token, Railway token, session secret, registry credential, or private document
in this repository.

## Content-only composition

The starter selects Furatena's packaged `docs` layout and supported `lagoon`
skin through the versioned presentation contract in `app/docs.yaml`. The
repository owns content, mounts/navigation, product copy, stable
tenant/workspace/site identity, and safe visual preferences. The installed
runtime owns the complete shell, view matrix, scripts, fonts, and presentation
assets. The starter's `app/theme/` directory contains only adopter-owned
branding, not a replacement shell or view matrix.

To customize presentation, add only the specific templates or assets your site
owns as documented in Furatena's presentation-pack guide. Do not copy the
packaged layout into this repository: sparse adopter overrides remain higher
precedence and cannot be overwritten by a runtime upgrade.

Existing starter repositories can migrate by adding the `presentation` and
`identity` blocks from `app/docs.yaml`, removing `shell`, `views`,
`theme.templates`, `theme.use`, and `theme.id`, then deleting the old
`app/theme/shell.html` and `app/theme/views/` copies. Keep any genuinely
adopter-owned branding or sparse overrides and validate them before deletion.

The managed deployment already validates `FURA_CONTENT_REPOSITORY` as a
credential-free, allowlisted HTTPS URL. That deployment identity is not yet
projected into the page-level provenance of the starter's filesystem mount, and
`docs.yaml` has no separate supported `source_edit_url` field. Consequently the
packaged reader does not currently add a repository edit link. This README's
GitHub editing workflow remains the source-edit path; the starter deliberately
does not duplicate the repository URL through an invented configuration key.

## Edit in GitHub

Open a Markdown file under `app/content/`, choose **Edit this file** in GitHub,
make the change, and create a branch. Open a pull request so reviewers can check
links, navigation, accuracy, and whether the page is safe to publish. Merge the
pull request only after those checks pass.

## Edit locally

Clone your repository, create a branch, edit Markdown under `app/content/`, and
preview the diff before pushing:

```console
git clone https://github.com/your-org/your-docs.git
cd your-docs
git switch -c docs/update-quickstart
git diff --check
git push --set-upstream origin docs/update-quickstart
```

The content-only repository intentionally has no local Furatena dependency.
Use the deployed preview or your team's separately documented runtime tooling
for rendered review.

## Publish content

Configure these GitHub Actions secrets:

- `FURATENA_REFRESH_URL`: the deployment's `/_fura/content/refresh` URL;
- `FURATENA_REFRESH_TOKEN`: the independently generated content refresh bearer.

The included workflow sends the reviewed `github.sha`, the currently active
commit, and a run-scoped idempotency key after a push to `main`, or when a
maintainer starts it manually. Furatena proves that exact commit is reachable
under the configured `FURA_CONTENT_REF`, validates and freezes a complete
generation, and switches generations atomically. The accepting process restarts
to load the new generation; the application image and deployment identity do not
change.

HTTP 202 returns an operation/status URL. Treat `stale_active_commit`,
`unreachable_commit`, `idempotency_key_collision`, and `refresh_in_progress` as
409 conflicts requiring a fresh operator decision.

An empty-body request exists only as v1 migration compatibility and is not used
by this starter because it lacks caller-selected exact-commit and idempotency
controls. Webhook delivery remains deferred beyond v1.

Confirm the new resolved content commit in `/meta.json` and verify the page in
reader, search, catalog, and agent outputs.

The `main` ref is the normal content channel. Maintainers of the conformance
fixture also keep a `conformance-v2` tag solely so the automated harness can
prove content update and rollback behavior.

## Roll back content

Content rollback is independent from image rollback. An operator can select the
last-known-good content generation with the same bearer authority:

```console
curl --fail --silent --show-error --request POST \
  --header "Authorization: Bearer $FURATENA_REFRESH_TOKEN" \
  "$FURATENA_ORIGIN/_fura/content/rollback"
```

After the service restarts, verify `/readyz`, the active content generation in
`/_fura/content/status`, and the unchanged image digest in `/meta.json`.

## Runtime updates

Furatena maintainers publish application releases as private image digests.
Updating this repository never updates that image. Apply an approved runtime
digest through the Railway service or template release process, preserve the
previous digest as the image rollback target, and verify that the adopter-owned
content generation remains selected.
