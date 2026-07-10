# CI Lanes

Furatena exposes each validation surface as a reproducible Make target. Run
`make install` once, then use the same commands locally that GitHub Actions
uses. Runtime estimates are for a warm local dependency cache and are intended
for scheduling, not as enforced performance thresholds.

| Lane | Local command | Scope | Extra dependency | Expected runtime |
| --- | --- | --- | --- | --- |
| Fast | `make ci-fast` | Ruff (including public return annotations), zero-diagnostic typed boundaries, owned ty diagnostic ratchets, and core catalog/config/theme unit tests | None beyond `make install` | ~30 seconds |
| Contract | `make ci-contract` | Structured `fura check`, authorization, content, response-shape, template, CSP, and boost contracts | None beyond `make install` | ~60 seconds |
| Coverage | `make ci-coverage` | Branch coverage and per-module ratchets for graph, access, export, and loader foundations | None beyond `make install` | ~60 seconds |
| Export | `make ci-export` | Static-export and DCP worker tests, a production-shaped Pages build, and an artifact URL crawl | None beyond `make install` | ~3 minutes |
| Browser | `make ci-browser` | Complete real-browser search, navigation, authoring, and responsive regression tier | `uv run playwright install chromium` | ~90 seconds |
| Agent | `make ci-agent` | MCP/resource lint, adapter parity, agent safety, and deterministic eval tests | None beyond `make install` | ~30 seconds |
| Release | `make ci-release` | Clean wheel/sdist build, archive audit, and isolated install smoke | None beyond `make install` | ~3 minutes |

Short aliases are available for `make fast`, `make contract`, `make coverage`, `make browser`,
`make browser-smoke`, `make browser-authoring`, `make browser-responsive`, `make agent`, and `make release`. The existing `make export` command remains a
direct product export; use `make ci-export` for the complete export CI lane.

The lanes are intentionally independent so CI jobs can run in parallel and
retain a clear failure owner. `make test` remains the full pytest suite and is
the final local fallback when a change crosses multiple surfaces.

The contract lane also runs `fura docs-reference --check`. Parser, default,
configuration, or environment drift fails until the generated CLI/configuration
reference is refreshed and reviewed with the implementation change.

## Ty diagnostic ratchets

`make ty-audit` runs ty 0.0.57 across `src/` and emits a structured report
without failing on the existing broad backlog. The categorized baseline lives in
`config/ty-diagnostics.json`; every diagnostic-bearing module records its rule
counts, owning area, root cause, actionable/upstream disposition, and likely
false-positive status. This keeps the full audit visible without turning an
unrelated edit into a repository-wide type-check wall.

`make ty-ratchet` is the blocking incremental gate used by `make ci-fast`.
Owned budgeted modules may reduce diagnostics but cannot increase their total,
increase an existing rule, or introduce a new rule. Zero-diagnostic modules may
not acquire any finding. The first wave owns configuration/public-schema
parsing budgets for `config.py` and `api_governance.py`, and adds the following
zero-diagnostic author/public-contract boundaries to the direct ty invocation:

- `catalog/author_store.py`
- `catalog/lifecycle.py`
- `catalog/models.py`
- `cli/authoring.py`
- `cli/contracts.py`

When a wave fixes findings, lower the affected module and rule budgets in the
same change. Never raise a budget merely to make CI pass. A ty upgrade requires
a fresh categorized broad audit and an intentional baseline review because
diagnostic semantics can change between versions.

## Branch gates and artifacts

Pull requests run the `fast`, `contract`, `coverage`, `release`, and
browser-smoke jobs for early lint, unit, hypermedia/diagnostic, core coverage,
distribution, and critical real-browser feedback. Pushes to `main` and manual
runs replace browser smoke with the full browser tier and add the `export` and
`agent` safety jobs.
GitHub Pages deploys only after all seven jobs pass.

Each job scopes the uv cache with its GitHub job name, so a cache or install
failure identifies one owning lane. The export job alone uploads the Pages
artifact, while the release job uploads a commit-named wheel/sdist artifact;
the browser job retains JUnit XML for 14 days, and the remaining jobs keep their
diagnostics in their named job logs. Browser tests have zero automatic retries:
a flaky failure remains visible and blocks the owning PR or main run.

The release lane clears stale distributions and ignores local uv source
overrides before building. It audits both archives against every runtime Python
module, schema, fixture, template, vendor script, and theme asset in `src/`,
then installs the wheel and sdist into separate temporary environments. Each
installed copy is exercised from outside the checkout with Python isolated mode,
no `PYTHONPATH`, and the GIL disabled; imports, entry points, package data, and
the declared package version must all match without a repository path on
`sys.path`. Finally, each installed artifact must scaffold a new app, pass a
strict content check, render a live request, freeze, and export the complete
static, asset, and agent-output surface.

Every Make lane runs Python with `PYTHON_GIL=0`, matching the workflow's
free-threaded CPython 3.14t runtime. The export lane clears deployment
URL/base-path variables for unit tests, then applies production defaults inside
the Pages build. Its final crawler parses rendered HTML, JSON sidecars, sitemap
XML, and navigable text links; it fails on repeated or escaped base paths,
incorrect canonical origins, and missing targets while naming the source
artifact and public referrer. Export also derives unique canaries for every
draft, private, protected, and archived source, then scans all generated files
(including extracted PDF text and inventory payloads) for policy leaks.

Version tags use the separate `release.yml` workflow. It accepts only an exact
`vMAJOR.MINOR.PATCH` matching package metadata on a commit reachable from
`main`. Unprivileged jobs rerun the fast, contract, agent, and isolated release
lanes and produce checksums. Separate jobs then generate GitHub provenance,
publish through PyPI OIDC, and create the GitHub release with generated notes;
project code never runs in the PyPI credential-bearing job. See
[RELEASING.md](RELEASING.md) for setup, verification, rollback, and compromise
procedures.

## Core coverage ratchets

`make ci-coverage` records branch coverage in `.coverage-core.json`, prints the
normal coverage report, and checks each foundational module against
`config/core-coverage.json`. It points artifact-dependent smoke tests at a fresh,
absent temporary frozen path so clean CI and developer workspaces execute the
same tests; pre-existing `app/frozen` output cannot inflate the measurement.
The policy stores the measured baseline and a
whole-number minimum separately. A change may raise a minimum after tests add
durable coverage, but must not lower one merely to make CI pass; a decrease
requires an explicit rationale in the pull request and an updated baseline.

The initial CPython 3.14t, GIL-disabled baselines recorded on 2026-07-06 are:

- `catalog/access.py`: 89.00% (minimum 88%)
- `catalog/export.py`: 90.18% (minimum 90%)
- `catalog/graph.py`: 86.67% (minimum 86%)
- `catalog/graph_schema.py`: 84.64% (minimum 84%)
- `catalog/loader.py`: 76.56% (minimum 76%)

The browser suite uses Playwright's async API. Playwright's synchronous API
crosses a greenlet bridge that segfaulted in the Linux 3.14t job with the GIL
disabled; direct asyncio calls avoid that bridge while preserving the same
browser coverage and the repository-wide free-threading requirement.

## Browser test tiers

Every real-browser test carries the base `browser` marker, the `browser_full`
regression marker, and at least one purpose marker. `tests/test_browser_tiers.py`
enforces that contract so new browser tests cannot silently bypass tiered runs.

| Tier | Marker | Command | Intended use |
| --- | --- | --- | --- |
| Smoke | `browser_smoke` | `make ci-browser-smoke` | PR gate for the smallest critical search, navigation, and author live-reload paths |
| Authoring | `browser_authoring` | `make ci-browser-authoring` | Preview reload, studio save, and draft creation workflows |
| Responsive | `browser_responsive` | `make ci-browser-responsive` | Mobile and responsive layout/interaction checks |
| Full | `browser_full` | `make ci-browser-full` or `make ci-browser` | Complete browser regression set used by main CI |
