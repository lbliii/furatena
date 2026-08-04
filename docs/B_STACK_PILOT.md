# B-stack versioned-corpus pilot

The isolated pilot app in `config/pilots/b-stack/` composes documentation from
exactly eight public repositories: bengal, chirp, chirp-ui, kida, patitas,
pounce, rosettes, and zoomies. Purr is deliberately excluded because Furatena
supersedes that exploration.

Each mount reads `site/content` from its canonical HTTPS GitHub remote. `main`
is the moving `latest` edition; discovery selects at most three stable `v*`
tags in descending semantic-version order. The shared policy excludes
prereleases. Unique mount ids and URL prefixes keep graph and route identities
stable.

This configuration is not imported by `app/docs.yaml` or `app/mounts.yaml` and
therefore cannot alter the production catalog. Run it explicitly:

```console
PYTHON_GIL=0 fura --app-root config/pilots/b-stack check --content-only --json
PYTHON_GIL=0 fura --app-root config/pilots/b-stack freeze --json
PYTHON_GIL=0 fura --app-root config/pilots/b-stack serve --preview --json
```

Source sync is bounded by the three-release policy and uses the existing
operation lease, timeout, atomic promotion, source-state, edition discovery,
and immutable shard contracts. The committed configuration contains no local
checkout paths, credentials, tokens, or private sources.

The sanitized discovery and runtime snapshot in `docs/b-stack-pilot-v1.json`
records selected tag refs, peeled commit SHAs, shard sizes, phase timings, and a
preview RSS sample observed on 2026-08-03. The freeze produced 30 edition
contexts: four for seven mounts and two for rosettes, which currently has one
matching release tag. It indexed 775 current pages and produced 392,918,523
bytes of frozen output. Preview preflight and loopback health passed for all
eight mounts; the sampled process RSS after the health response was 317,952
KiB. These are one-run observations, not performance guarantees. Because
`main` is mutable, refresh the snapshot deliberately before treating a later
freeze as release evidence.

## Handoff to #357

#357 inherits one explicit blocker: `fura check --content-only` reaches the
full remote-backed composition but currently reports 20 source errors and 219
warnings. The errors are 16 upstream broken internal links, one unresolved
reference, one unknown collection, and two directive-nesting findings; the pilot
has zero template-wiring errors. #357 should repair
or deliberately route those source-owned findings without weakening checks,
then refresh the bounded refs and repeat the recorded freeze and preview
measurements.

## Deployed pilot smoke receipt

After the source diagnostics are resolved and an operator has deployed this
isolated configuration, verify it from outside the service with the exact
expected identities from the deployment and content-generation records:

```console
python scripts/verify-edition-pilot.py "$ORIGIN" \
  --manifest docs/b-stack-pilot-v1.json \
  --expected-build-sha "$EXPECTED_BUILD_SHA" \
  --expected-content-generation "$EXPECTED_CONTENT_GENERATION" \
  --expected-content-ref "$EXPECTED_CONTENT_REF" \
  --expected-image-digest "$EXPECTED_IMAGE_DIGEST" \
  --output "$RECEIPT_PATH"
```

The origin must be a credential-free HTTPS origin without a path. The verifier
does not discover or modify deployment resources. It fails unless
`/versions.json` matches all eight pinned mounts and editions, `/readyz` has
passing source and index checks for every mount, and `/meta.json` exactly
matches the supplied build SHA, active content generation and ref, and image
digest. It also exercises each mount's latest and historical routes, `latest`
and `stable` redirects, version-selector round trips, edition-scoped catalog
and semantic queries, and historical lifecycle banner. The Pounce proof uses
0.9.0 as the pre-fix edition and 0.9.2 as the fixed edition and requires a
non-empty mount-level Content IR diff.

The v1 receipt is timestamp-free and stable for identical inputs. It records
the manifest SHA-256, exact runtime and content identities, readiness check
counts, relative routes and result counts for every mount, and the Pounce diff
summary. Retain the receipt with the deployment record and the separate
`afdocs` result. A successful local or loopback run is useful during staging,
but it does not replace the required external-client receipt against the public
pilot origin.
