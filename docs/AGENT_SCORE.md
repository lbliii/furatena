# Agent-readiness score operations

Furatena measures the deployed GitHub Pages and Railway channels with
`afdocs@0.18.7`. The workflow is pinned because afdocs is pre-1.0 and its check
IDs, scoring, and output contract may change between minor releases.

## Current baseline

The committed baseline at `config/afdocs-baseline.json` was recorded by GitHub
Actions run `29110881180` on 2026-07-10. Six equivalent curated pages were
checked on each channel against Agent-Friendly Documentation Spec v0.5.0.

See [`STATIC_VS_LIVE_AGENT_READINESS.md`](STATIC_VS_LIVE_AGENT_READINESS.md)
for the channel capability analysis and publishing recommendation behind this
scorecard.

| Channel | URL | Score | Grade | Pass | Fail | Skip |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| Static | `https://lbliii.github.io/furatena` | 96 | A | 19 | 1 | 3 |
| Live | `https://furatena-production.up.railway.app` | 98 | A | 19 | 1 | 3 |

The required channel distinction is explicit:

- Static Pages fails `content-negotiation`: all six pages return HTML for
  `Accept: text/markdown`. Static hosting cannot vary a URL by request header.
- Live Railway passes `content-negotiation`: all six pages return Markdown with
  `Content-Type: text/markdown`.

The live channel currently fails `cache-header-hygiene`; the static channel
passes it. Both known failures remain in the per-check baseline and in every raw
artifact. A known failure does not make the workflow silently green: any lower
overall score, worse check status, missing/new check, tool/spec drift, or loss of
the required negotiation split fails the job.

## Workflow

`.github/workflows/agent-score.yml` runs:

- after a successful `main` validation and Pages deployment;
- every Monday at 06:17 UTC;
- by manual dispatch; and
- on pull requests that change the score workflow, comparator, baseline, or tests.

The workflow runs afdocs in a GitHub-hosted Node.js 24 runner. It uses a curated
six-page sample so results are repeatable and page-level scores remain meaningful.
The CLI may exit nonzero for known failed checks; the comparator, rather than the
raw CLI exit code, owns the regression decision.

Every run uploads a 90-day `agent-score-<run>-<attempt>` artifact containing:

- raw afdocs JSON for each channel;
- each raw CLI exit code as JSON;
- a combined report with scores, categories, diagnostics, and per-check details;
- a machine-readable regression result; and
- in record mode, a candidate baseline.

The GitHub job summary shows both scores and any regression messages. The job
itself fails when `scripts/check_afdocs_scores.py` reports a regression.

## Updating the baseline

Do not edit scores or statuses merely to make CI pass. A baseline update is a
review decision tied to a deliberate site, afdocs, or specification change.

1. Change the pinned afdocs version or deployed behavior in its own PR.
2. Run record mode after the workflow exists on the default branch:

   ```console
   gh workflow run agent-score.yml -f mode=record
   ```

3. Download the run artifact and inspect both raw reports, the combined report,
   and `afdocs-baseline.candidate.json`.
4. Confirm the live channel still passes content negotiation and static Pages
   remains a documented non-pass.
5. Replace `config/afdocs-baseline.json` with the reviewed candidate and rerun
   the workflow in `check` mode.

Score improvements and check-status improvements do not require a baseline
change. Keeping the older floor preserves the ratchet; update it later only when
the team intentionally wants the stronger result to become mandatory.
