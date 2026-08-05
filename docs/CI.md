# CI Lanes

Furatena exposes each validation surface as a reproducible Make target. Run
`make install` once, then use the same commands locally that GitHub Actions
uses. Runtime estimates are for a warm local dependency cache and are intended
for scheduling, not as enforced performance thresholds.

| Lane | Local command | Scope | Extra dependency | Expected runtime |
| --- | --- | --- | --- | --- |
| Fast | `make ci-fast` | Ruff 0.15.20 formatting and lint (including public return annotations), zero-diagnostic typed boundaries, owned ty diagnostic ratchets, steward-map integrity, and core catalog/config/theme unit tests | None beyond `make install` | ~30 seconds |
| Contract | `make ci-contract` | Structured `fura check`, authorization, content, response-shape, template, CSP, and boost contracts | None beyond `make install` | ~60 seconds |
| Public safety | `make ci-public-safety` | Visibility canaries, public-projection privacy, access isolation, RBAC, retrieval private/archived parity, preview HTML exclusion, export/freeze public-filter proofs, and the proof-map ratchet | None beyond `make install` | ~4 minutes |
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

## Formatter contract

Contributors must use Ruff 0.15.20, pinned exactly in both development dependency
sets. Run `make format` to apply the formatter to the repository's Python files
and `make format-check` for a read-only local check. The Fast CI lane owns this
scope: `make ci-fast` depends on `format-check`, so formatting drift fails before
lint, typing, or unit tests run. Formatter upgrades must remain isolated from
semantic changes and regenerate line-number-derived references before review.

## Branch gates and artifacts

Pull requests always run the `fast`, `contract`, and `public-safety` jobs for
early lint, unit, hypermedia, diagnostic, and audience-safety feedback. Draft
pull requests skip expensive lanes: coverage, browser, release, private-image,
PDF, and Railway preview-controller work do not start until the pull request
leaves draft state. Converting a pull request to draft publishes a removed
preview report and skips queued preview work. Each pull request uses one
workflow concurrency group keyed by PR number so a new push cancels superseded
untrusted work without touching trusted main publication or image lifecycle
runs. The contract and public-safety lanes wait for the fast lane on every
event so a lint or unit failure does not start coverage, browser, release, or
audience-proof work. The fast job classifies the complete
base-to-head path diff—including additions, copies, modifications, renames,
deletions, and type changes—and adds `coverage` for Python/test/coverage-policy
changes, browser smoke for content/render/theme/browser changes, and `release`
for source or packaging changes. `ready_for_review` uses the same diff
contract as `synchronize`; diff resolution failures fail closed with an
actionable diagnostic. Pushes to `main` and manual runs force all eight
primary lanes, replace browser smoke with the full browser tier, and add the
`export` and `agent` safety jobs. GitHub Pages deploys only after the required
jobs pass.
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

The separate `private-image.yml` workflow publishes the proprietary commercial
artifact when an image input changes. A qualifying merge to `main` builds one
candidate, adds an SBOM and provenance,
scans the published digest, and pulls and boots that exact subject in a clean
job. After every exact-digest check passes, the smoke job stores a strict
promotion receipt for that workflow run and attempt. Protected manual promotion
rechecks the successful current run metadata and rejects missing, stale, or
mismatched evidence before selecting an existing digest without rebuilding it.
Protected manual operations promote, deprecate, or revoke existing digests
without rebuilding them. Rollback and replacement inputs resolve through their
exact stable release records and must remain non-revoked, registry-available,
and attested. Deprecation also proves that the named stable version owns its
digest; emergency revocation retains that durable association even when the
affected subject is unavailable. The open-source package publishes to PyPI via
Trusted Publishing ([PYPI.md](PYPI.md)); the isolated wheel and sdist lane
remains the packaging-integrity check used by both CI and the release gate.
See [RELEASING.md](RELEASING.md) for registry setup, verification, promotion,
rollback, revocation, and compromise procedures for any remaining image path.

The external production evidence workflow runs every six hours, keeps receipts
for 30 days, and cancels a superseded probe. Five-minute availability sampling
belongs in a dedicated uptime service; GitHub Actions retains the slower,
auditable artifact-integrity receipt and operational-issue routing.

## Public-safety proof map

Visibility and public-projection ownership across live, frozen, static, search,
PDF, inventory, DCP, agent, MCP, CLI, preview, and develop surfaces is recorded
in [PUBLIC_SAFETY_PROOF_MAP.md](PUBLIC_SAFETY_PROOF_MAP.md) (#583). Run
`make ci-public-safety` for the focused allowed/forbidden audience lane; keep
`make ci-export` as the Pages canary + URL artifact Protect.

## CI event brakes

Wave 1 of the CI cost saga (#577) adds deterministic brakes so agent-heavy pull
requests stop paying for obsolete or premature proof.

| Event | Cheap lanes (`fast`, `contract`, `public-safety`) | Expensive lanes | Preview / image / PDF |
| --- | --- | --- | --- |
| Draft open, sync, or push | Run | Skipped | Skipped |
| Convert to draft | Run if triggered | Skipped | Preview report publishes `removed` |
| Ready for review | Run | Same base-to-head diff as sync | Runs when not draft |
| Superseded push on same PR | New run; prior run canceled | Canceled with workflow | Canceled with workflow |
| Push to `main` | All lanes | All lanes | N/A (trusted publication) |

**Pre-change baseline (2026-08-01 through 2026-08-04):** 966 workflow runs,
3,491 job records, and 2,471 unrounded hosted-runner minutes in roughly 29
hours during agent-heavy development. General validation was 54.3% of cost; PR
preview reporting and Railway control 21.8%; private-image proof 15.6%; PDF
proof 6.9%.

**Post-change verification (when hosted runners are available):**

1. Open a draft PR that touches `src/**` and confirm only `fast` and `contract` start.
2. Mark the PR ready and confirm expensive lanes match the path diff.
3. Push two commits quickly on a ready PR and confirm the older workflow run shows `cancelled`.
4. Convert the PR to draft and confirm preview reporting publishes `removed` without Railway work.
5. Compare unrounded runner minutes for steps 1–4 against the baseline window.

**Rollback:** Revert the workflow commits on `.github/workflows/pages.yml`,
`.github/workflows/private-image.yml`, `.github/workflows/pdf-proof.yml`, and
`.github/workflows/preview-report.yml`, then restore the prior `docs/CI.md`
event-brake section. No product runtime or schema migrations are involved.

## Repository hygiene

`make hygiene` validates Towncrier fragment names and content, requires
release-note intent on pull requests, rejects silent `except` paths through
Ruff `S110`/`S112`, and ratchets statically visible raise messages against
`docs/raise-message-baseline.txt`. It is a dependency of `make ci-fast`.

The reusable per-module coverage design is documented in
[COVERAGE_RATCHET.md](COVERAGE_RATCHET.md), including the measured-floor and
stale-baseline rules needed to copy the lane safely.

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
| htmx 4 preview | `browser_htmx4` | `make ci-browser-htmx4-preview` | Exact beta5 compatibility evidence; does not change the default runtime |
| Full | `browser_full` | `make ci-browser-full` or `make ci-browser` | Complete browser regression set used by main CI |
