# CI Lanes

Furatena exposes each validation surface as a reproducible Make target. Run
`make install` once, then use the same commands locally that GitHub Actions
uses. Runtime estimates are for a warm local dependency cache and are intended
for scheduling, not as enforced performance thresholds.

| Lane | Local command | Scope | Extra dependency | Expected runtime |
| --- | --- | --- | --- | --- |
| Fast | `make ci-fast` | Ruff plus core catalog, config, and theme unit tests | None beyond `make install` | ~20 seconds |
| Contract | `make ci-contract` | Structured `fura check`, authorization, content, response-shape, template, CSP, and boost contracts | None beyond `make install` | ~60 seconds |
| Export | `make ci-export` | Static-export and DCP worker tests plus a production-shaped Pages artifact build | None beyond `make install` | ~3 minutes |
| Browser | `make ci-browser` | Real-browser author preview, live reload, save, and create flows | `uv run playwright install chromium` | ~60 seconds |
| Agent | `make ci-agent` | MCP/resource lint, adapter parity, agent safety, and deterministic eval tests | None beyond `make install` | ~30 seconds |
| Release | `make ci-release` | Wheel/sdist build and CLI entry-point smoke | None beyond `make install` | ~3 minutes |

Short aliases are available for `make fast`, `make contract`, `make browser`,
`make agent`, and `make release`. The existing `make export` command remains a
direct product export; use `make ci-export` for the complete export CI lane.

The lanes are intentionally independent so CI jobs can run in parallel and
retain a clear failure owner. `make test` remains the full pytest suite and is
the final local fallback when a change crosses multiple surfaces.
