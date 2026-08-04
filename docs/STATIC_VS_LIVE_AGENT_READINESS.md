# Static versus live agent readiness

This analysis compares Furatena's GitHub Pages export with the Railway service
using the evidence recorded on 2026-07-10. It is the decision record for
[saga #290](https://github.com/lbliii/furatena/issues/290) and its dogfooding
epic, [#293](https://github.com/lbliii/furatena/issues/293).

## Decision

Publish both channels from the same source. Use Railway as the default endpoint
for interactive agent clients and GitHub Pages as the durable read-only fallback.
Do not remove Pages: it remains the simpler, more cacheable browser surface and a
complete source for bulk sidecars. The Pounce 0.9.2 production verification
recorded on 2026-07-13 completed full identity-encoded catalog delivery, so
Railway is also ready to serve large bulk bodies.

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

The accepted managed Railway profile separates the immutable Furatena image
from adopter-owned public Git content. An authenticated refresh request names
one exact commit reachable from the service's configured ref. Furatena stages
and validates the checkout, freezes the browser, search, catalog, and agent
artifacts together, verifies their generation manifest, and atomically advances
the active selector while retaining last-known-good.

Promotion does not mutate the process that accepted the request. The durable
operation reports `activation_pending_restart`, optionally schedules a graceful
restart, and becomes `ready` only after startup proves that the running
generation, image digest, and build commit match the promoted receipt. Content
publication can therefore keep the same image digest and Railway deployment ID,
but the single-replica process still restarts to load one coherent generation.

HTTP and `fura content refresh` share the same provider-neutral operation model.
The v1 HTTP credential is scoped to one configured service/site and cannot
override repository, ref, subdirectory, mount subset, or actor identity. Every
refresh rebuilds all mounts and reached frozen/static/agent artifacts for that
site. Webhook, scheduler, private-repository, multi-site, and multi-replica
transports require separate approved trust and coordination contracts.

Git-backed author and hybrid mounts remain a different mechanism: they can sync
during catalog construction and reindex local file changes, but they are not the
managed production publication authority.

## Recommendation and follow-ups

Adopt this default publishing policy:

1. Build Railway and Pages from the same reviewed commit.
2. Advertise Railway as the canonical interactive agent endpoint.
3. Advertise Pages as the durable browser and bulk-artifact fallback.
4. Keep the dual-channel Agent Score ratchet as the release guard.

The recommendation implies these follow-ups:

- [#329](https://github.com/lbliii/furatena/issues/329): completed on 2026-07-13
  with Pounce 0.9.2 production identity and full-body transfer evidence.
- [#436](https://github.com/lbliii/furatena/issues/436): retain the external
  Railway same-deployment proof separately from the shipped provider-neutral
  refresh contract.
- expose and secure a deployed MCP transport before describing the current
  Railway web service itself as an MCP endpoint; until then, use `fura mcp` as a
  separate live process and treat `tools.json` as discovery metadata.
