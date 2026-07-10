# Static versus live agent readiness

This analysis compares Furatena's GitHub Pages export with the Railway service
using the evidence recorded on 2026-07-10. It is the decision record for
[saga #290](https://github.com/lbliii/furatena/issues/290) and its dogfooding
epic, [#293](https://github.com/lbliii/furatena/issues/293).

## Decision

Publish both channels from the same source. Use Railway as the default endpoint
for interactive agent clients and GitHub Pages as the durable read-only fallback.
Do not remove Pages: it remains the simpler, more cacheable browser surface and a
complete source for bulk sidecars. Before directing bulk catalog consumers to
Railway, deploy the Pounce response-streaming fix tracked in
[#329](https://github.com/lbliii/furatena/issues/329).

This is a channel policy, not a content fork. The shared freeze and export path
continues to produce both outputs.

## Scorecard

GitHub Actions run
[`29110881180`](https://github.com/lbliii/furatena/actions/runs/29110881180)
recorded the baseline with afdocs 0.18.7 against Agent-Friendly Documentation
Spec v0.5.0. Six equivalent pages were sampled on each channel.

| Channel | Score | Grade | Pass | Fail | Skip | Known non-pass |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| GitHub Pages | 96 | A | 19 | 1 | 3 | `content-negotiation` |
| Railway | 98 | A | 19 | 1 | 3 | `cache-header-hygiene` |

The two-point difference is meaningful but narrow. Railway returned Markdown
with `Content-Type: text/markdown` for `Accept: text/markdown` on all six pages.
Pages returned HTML because a static host cannot vary one URL by an HTTP request
header. Pages did better on cache-header hygiene in the same run.

The complete per-check contract is committed in
[`config/afdocs-baseline.json`](../config/afdocs-baseline.json). The scheduled
lane, retention policy, and regression rules are documented in
[`docs/AGENT_SCORE.md`](AGENT_SCORE.md).

## Capability comparison

The afdocs score covers page readability. It does not measure the request-time
catalog features that distinguish a live application from a static export.
Direct production probes on 2026-07-10 produced this comparison:

| Capability | GitHub Pages | Railway | Finding |
| --- | --- | --- | --- |
| Page HTML and `.md` sidecars | Yes | Yes | Shared delivery code keeps the readable representations aligned. |
| `Accept: text/markdown` | No | Yes | Requires request-time content negotiation. |
| Bulk `catalog.json`, `semantic.json`, `search.json`, `tools.json`, and `llms.txt` | Yes | Yes | Pages is a strong immutable distribution channel for complete snapshots. |
| Filtered `/catalog/query.json` | No; the route returns 404 | Yes; filters, edges, and pagination execute against the loaded graph | Requires application code at request time. |
| `/search/semantic?q=` | No; the route returns 404 | Yes; hybrid ranked results are computed from the loaded catalog and embedding index | Pages publishes `semantic.json`, but the client must supply its own query engine. |
| MCP tools and resources | Manifest only | Requires a live Furatena MCP process | `tools.json` is available on both channels, but a static host cannot execute the MCP protocol. The current Railway web service does not yet expose an MCP transport. |

Pages remains especially good at immutable, CDN-friendly delivery. It published
the multi-megabyte `catalog.json` and `semantic.json` snapshots successfully in
the probe, has no application health dependency, and passed the afdocs cache
check. Agents that can download and process sidecars locally lose little by using
it.

Railway is better when the client needs the server to do work: negotiate a
representation, filter graph records, rank a semantic query, retrieve a node, or
run MCP tools. Its current `tools.json` describes those tool contracts, while an
actual MCP transport remains deployment work rather than an HTTP route in the
present service.

## Runtime re-sync finding

The production Railway service is not a living source-sync deployment today. Its
Docker build runs `fura freeze`, sets `FURA_MODE=preview`, and packages the frozen
catalog into the image. Preview mode deliberately skips git source sync and live
reload. Content changes therefore reach production only through a new image and
deployment.

The code supports two adjacent mechanisms, but they do not close that gap:

- `CatalogRegistry` calls `sync_git_source` while it is constructed in author or
  hybrid mode. That fetches and atomically promotes a git snapshot.
- author/hybrid watchers reindex changed files already present under a mounted
  content root.

There is no runtime endpoint, scheduler, or control-plane action that fetches a
new git ref and swaps the running registry. Issue
[#318](https://github.com/lbliii/furatena/issues/318) remains the design and
demonstration task for that mechanism. Until it lands, "live" means dynamic
queries over a deploy-time snapshot, not content publication without a deploy.

## Recommendation and follow-ups

Adopt this default publishing policy:

1. Build Railway and Pages from the same reviewed commit.
2. Advertise Railway as the canonical interactive agent endpoint.
3. Advertise Pages as the durable browser and bulk-artifact fallback.
4. Keep the dual-channel Agent Score ratchet as the release guard.

The recommendation implies these follow-ups:

- [#329](https://github.com/lbliii/furatena/issues/329): ship the Pounce fix
  before making Railway the default for large identity-encoded catalog bodies.
- [#318](https://github.com/lbliii/furatena/issues/318): design and demonstrate a
  controlled git re-sync trigger if no-redeploy publication remains a product
  requirement.
- expose and secure a deployed MCP transport before describing the current
  Railway web service itself as an MCP endpoint; until then, use `fura mcp` as a
  separate live process and treat `tools.json` as discovery metadata.
