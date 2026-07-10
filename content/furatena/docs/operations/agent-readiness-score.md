---
title: Continuous agent-readiness scoring
owner: docs-product
reviewed_at: "2026-07-10"
description: Compare and ratchet Agent Score across static and live delivery
draft: false
weight: 55
lang: en
type: doc
tags: [agents, ci, github-pages, railway, scorecard]
category: operations
---

Furatena continuously measures how well agents can discover and consume the same
documentation through the static GitHub Pages channel and the live Railway
channel. The scheduled CI lane runs 23 afdocs checks on six equivalent pages,
retains the complete JSON evidence, and fails when either channel regresses.

## Recorded baseline

The first baseline was recorded on 2026-07-10 with afdocs 0.18.7 and
Agent-Friendly Documentation Spec v0.5.0.

| Channel | Score | Grade | Passed checks | Failed checks | Skipped checks |
| --- | ---: | --- | ---: | ---: | ---: |
| GitHub Pages | 96 | A | 19 | 1 | 3 |
| Railway | 98 | A | 19 | 1 | 3 |

The two-point gap captures a real delivery difference. The live service returns
Markdown with the correct media type for `Accept: text/markdown` on all six
sampled pages. Pages returns HTML for the same request because a static host
cannot negotiate two representations at one URL.

The complete baseline also records every check status. At the time of recording,
Railway failed cache-header hygiene while Pages passed it. Known failures remain
visible rather than being removed from the check set.

## Regression policy

The lane fails for any of these changes:

- a channel's overall score falls below its committed baseline;
- a check moves to a worse status;
- a baseline check disappears or a new check appears without review;
- the afdocs version or implemented specification changes; or
- live content negotiation stops passing.

An improved score or check status passes without weakening the older floor. A
new baseline is committed only after inspecting the retained raw reports and
accepting the changed contract.

## Evidence

Runs produce a 90-day GitHub Actions artifact with raw reports for both channels,
the combined score and per-check details, raw command exit states, and the
regression decision. The workflow also publishes the channel table and any
regressions in the GitHub job summary.

See the repository's `docs/AGENT_SCORE.md` for operator commands and baseline
update procedure. For the delivery capabilities behind the score, continue to
[[docs/concepts/static-vs-live-agent-readiness|Static versus live agent readiness]],
[[docs/operations/deploy|Deploy]] and
[[docs/operations/consume-agent-outputs|Consume agent outputs and MCP]].
