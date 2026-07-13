# Pounce 0.9 deployment-boundary verification

This record tracks the evidence for Furatena
[#329](https://github.com/lbliii/furatena/issues/329). The verification was
refreshed on 2026-07-13 against `bengal-pounce==0.9.2` with free-threaded
CPython 3.14t.

## Completed evidence

The repository pins Pounce 0.9 as a direct production dependency. The old
`FURA_KEEP_ALIVE_TIMEOUT=75` workaround is removed because 0.9 fixes active
HTTP/2 response reaping and slow flow-control drains. Railway replacement
configuration now makes the lifecycle window explicit: five seconds of overlap
and 15 seconds for the retiring deployment to drain.

`tests/test_pounce_wire_contract.py` exercises the installed release at the
actual HTTP listener boundary:

- built-in readiness GET and HEAD return the same status, content type, content
  length, and cache policy;
- HEAD sends exactly zero body octets while preserving GET's nonzero
  `Content-Length`;
- a two-thread sync worker generation performs `graceful_reload()` under sampled
  traffic, hands off the listener, advances the reported generation, and records
  only HTTP 200 observations with monotonic timestamps; and
- shutdown preserves an in-flight slow response and gives new requests a bounded
  HTTP 503 with `{"status":"draining"}`.

Pounce 0.9.1 closes the listener-boundary gap tracked by
[Pounce #308](https://github.com/lbliii/pounce/issues/308). Late HTTP/1 GET and
HEAD requests to the configured `/readyz` path now keep the structured JSON 503
contract in async workers and the shared multi-worker accept distributor. The
former expected failure is now a required passing wire assertion.

Pounce 0.9.2 closes the buffered-response gap tracked by
[Pounce #312](https://github.com/lbliii/pounce/issues/312). Sync workers now
complete a response when `socket.sendmsg()` accepts only a prefix of the header
and body iovec under downstream backpressure.

## Railway canary

The dedicated `pounce-railway-smoke` service was redeployed from Pounce `main`
at commit `01fa0d6e8edefd95397a6b20b9a911e56d541a7b`. Deployment
`3a82b8c4-2080-48b5-8ed8-6af5bcb37839` reached terminal `SUCCESS` and reported:

- Pounce 0.9.0;
- Python 3.14.3 with the GIL disabled;
- built-in `/readyz` over Railway's public HTTP/2 edge; and
- the expected `main-canary` deployment channel and commit identity.

Continuous public readiness probes stayed on HTTP 200. Bounded Railway HTTP logs
showed that every recorded probe was routed to the newly admitted deployment.
This is the correct edge behavior, but it also means the public domain cannot
observe the old instance after Railway begins draining it. The direct listener
test above owns the retiring-instance contract.

## Pounce 0.9.1 production attempt

Railway deployment `9d46b98b-20e7-4c6a-b660-d768a0f931c1` reached terminal
`SUCCESS` for Furatena commit
`5998e2d9337cf545b16d62701b360c88ad6b54ca`. `/meta.json` reported
`bengal-pounce` 0.9.1 and the expected commit, while `/healthz`, `/readyz`, and
the smaller runbook surfaces returned HTTP 200.

The bulk-artifact gate correctly blocked promotion. `/catalog.json` declared
`Content-Length: 2568040`, but both downstream HTTP/2 and forced HTTP/1.1
received only 37,056 bytes. Railway's HTTP log recorded 38,320 transmitted
bytes, while Pounce's access log recorded the full 2,568,040-byte application
body. That evidence localized the loss to Pounce's sync-worker socket write and
resulted in Pounce #312 and the 0.9.2 patch release.

## Pounce 0.9.2 production proof

Railway deployment `9c9b4445-9329-4a6a-b037-7f2a07d7e986` reached terminal
`SUCCESS` for the reviewed Furatena commit
`50f105c1fcfb6760f9e53ad4106f844583b5ea57`. Startup telemetry reported Pounce
0.9.2 on Python 3.14.3 with the GIL disabled. `/meta.json` independently
reported the expected git SHA, `bengal-pounce` 0.9.2, and freeze fingerprint
`be461715dfbcb5a4`.

The production artifact verifier completed without truncation and reported:

- 269 catalog pages;
- 100 pages in the default paginated graph-query response, with `total: 269`;
- 267 searchable pages;
- 2,024 semantic chunks; and
- 516,116 bytes in `llms-full.txt`.

A separate forced HTTP/1.1 identity transfer received all 2,568,040 bytes of
`/catalog.json`, closing the exact failure observed under Pounce 0.9.1.
`/healthz`, `/readyz`, catalog query, semantic search, tool discovery, sitemap,
and Markdown page probes all returned HTTP 200 through Railway's production
edge.

The public edge cannot observe a retiring instance after Railway removes it
from routing. The direct listener test therefore remains the authoritative
proof for the bounded drain-time 503 contract, while this deployment proves
admission, identity, health, and complete production response delivery. All
acceptance evidence for #329 is complete.
