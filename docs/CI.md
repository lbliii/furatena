# CI Lanes

Furatena exposes each validation surface as a reproducible Make target. Run
`make install` once, then use the same commands locally that GitHub Actions
uses. Runtime estimates are for a warm local dependency cache and are intended
for scheduling, not as enforced performance thresholds.

| Lane | Local command | Scope | Extra dependency | Expected runtime |
| --- | --- | --- | --- | --- |
| Fast | `make ci-fast` | Ruff plus core catalog, config, and theme unit tests | None beyond `make install` | ~20 seconds |
| Contract | `make ci-contract` | Structured `fura check`, authorization, content, response-shape, template, CSP, and boost contracts | None beyond `make install` | ~60 seconds |
| Coverage | `make ci-coverage` | Branch coverage and per-module ratchets for graph, access, export, and loader foundations | None beyond `make install` | ~60 seconds |
| Export | `make ci-export` | Static-export and DCP worker tests, a production-shaped Pages build, and an artifact URL crawl | None beyond `make install` | ~3 minutes |
| Browser | `make ci-browser` | Real-browser author preview, live reload, save, and create flows | `uv run playwright install chromium` | ~60 seconds |
| Agent | `make ci-agent` | MCP/resource lint, adapter parity, agent safety, and deterministic eval tests | None beyond `make install` | ~30 seconds |
| Release | `make ci-release` | Wheel/sdist build and CLI entry-point smoke | None beyond `make install` | ~3 minutes |

Short aliases are available for `make fast`, `make contract`, `make coverage`, `make browser`,
`make agent`, and `make release`. The existing `make export` command remains a
direct product export; use `make ci-export` for the complete export CI lane.

The lanes are intentionally independent so CI jobs can run in parallel and
retain a clear failure owner. `make test` remains the full pytest suite and is
the final local fallback when a change crosses multiple surfaces.

## Branch gates and artifacts

Pull requests run the `fast` and `contract` jobs for early lint, unit, and
hypermedia/diagnostic feedback. Pushes to `main` and manual runs add the
`export`, `browser`, `agent`, and `release` safety jobs. GitHub Pages deploys
only after all seven jobs pass.

Each job scopes the uv cache with its GitHub job name, so a cache or install
failure identifies one owning lane. The export job alone uploads the Pages
artifact, while the release job uploads a commit-named wheel/sdist artifact;
the remaining jobs keep their diagnostics in their named job logs.

Every Make lane runs Python with `PYTHON_GIL=0`, matching the workflow's
free-threaded CPython 3.14t runtime. The export lane clears deployment
URL/base-path variables for unit tests, then applies production defaults inside
the Pages build. Its final crawler parses rendered HTML, JSON sidecars, sitemap
XML, and navigable text links; it fails on repeated or escaped base paths,
incorrect canonical origins, and missing targets while naming the source
artifact and public referrer. Export also derives unique canaries for every
draft, private, protected, and archived source, then scans all generated files
(including extracted PDF text and inventory payloads) for policy leaks.

## Core coverage ratchets

`make ci-coverage` records branch coverage in `.coverage-core.json`, prints the
normal coverage report, and checks each foundational module against
`config/core-coverage.json`. The policy stores the measured baseline and a
whole-number minimum separately. A change may raise a minimum after tests add
durable coverage, but must not lower one merely to make CI pass; a decrease
requires an explicit rationale in the pull request and an updated baseline.

The initial CPython 3.14t, GIL-disabled baselines recorded on 2026-07-06 are:

- `catalog/access.py`: 89.00% (minimum 88%)
- `catalog/export.py`: 91.67% (minimum 91%)
- `catalog/graph.py`: 86.67% (minimum 86%)
- `catalog/graph_schema.py`: 85.39% (minimum 85%)
- `catalog/loader.py`: 79.41% (minimum 79%)

The browser suite uses Playwright's async API. Playwright's synchronous API
crosses a greenlet bridge that segfaulted in the Linux 3.14t job with the GIL
disabled; direct asyncio calls avoid that bridge while preserving the same
browser coverage and the repository-wide free-threading requirement.
