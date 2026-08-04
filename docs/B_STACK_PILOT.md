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
