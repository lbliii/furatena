# Pounce 0.9 deployment-boundary verification

This record tracks the evidence for Furatena
[#329](https://github.com/lbliii/furatena/issues/329). The verification was
refreshed on 2026-07-13 against `bengal-pounce==0.9.1` with free-threaded
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

## Remaining production proof

Furatena production remains on Pounce 0.8.2 until the Pounce 0.9.1 upgrade is
explicitly approved for rollout. After deployment, do not close #329 until all
of these pass:

1. Railway reports terminal `SUCCESS` for the exact reviewed commit.
2. `/meta.json` reports `bengal-pounce` 0.9.1 and the expected git SHA.
3. `python scripts/verify-live-artifacts.py "$ORIGIN"` completes for every
   identity-encoded bulk artifact without truncation.
4. `/healthz` and the configured `/readyz` path pass through Railway's production
   protocol.
