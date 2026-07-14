# Railway PR-preview dogfood evidence

This record captures the first live dogfood run for the governed Railway
preview controller. PR #462 was the evidence vehicle. It changed only
documentation, tests, and preview-controller code; it did not change the
production Railway service configuration.

## Production isolation

Before the preview opened, production was healthy on
`dd5fcfd98b651742649e4b00116c9ea1edde2dc0`. After the workspace-token fix in
PR #463 merged, Railway deployed production from `main` at
`623637a1c9ab7b0bba0de130b4d18158591f19f8` and reached terminal `SUCCESS`.
The PR-preview activity did not replace the production environment or service.

## Observed preview lifecycle

Railway created the isolated environment `furatena-pr-462` with environment ID
`c42dd6bf-6e19-4783-85b9-796501521175` and the unique public domain
`furatena-furatena-pr-462.up.railway.app`.

Two immutable PR heads were observed:

| PR head | Deployment | Terminal state | Observation |
| --- | --- | --- | --- |
| `8fe8313d70b36e17ab7092dade76ef9efb6ef623` | `b99a0c58-cea3-49a7-bee5-927685f0ae6b` | `REMOVED` | Initial preview deployment. |
| `e6afe19e10fe1dc373feba1211eb04b549026d78` | `493334c5-9ee4-45b8-a2c6-729bfe65e9c8` | `SUCCESS` | Newer head superseded the initial deployment. |

A follow-up probe on 2026-07-20 observed `/readyz` returning HTTP 200 with all
readiness checks passing. `/meta.json` reported build SHA
`e6afe19e10fe1dc373feba1211eb04b549026d78` and freeze fingerprint
`9b8ea9bd5f4706a1`.

## Controller failure and resolution

The July 14 `Report Furatena PR preview` run did not complete governed preview
configuration. Trusted default-branch code still called `railway link`, which
the least-privilege workspace token could not authorize. The controller stopped
before setting preview identity, so `/preview-manifest.json` correctly remained
unavailable and authenticated human/agent conformance was not claimed.

PR #463 removed mutable linking, resolved the deterministic PR environment by
explicit project ID and name, and merged on 2026-07-20. Its focused tests,
`make ci-fast`, and `make ci-contract` passed locally under free-threaded Python.
Hosted jobs remained unavailable because the account could not start Actions
jobs; failed jobs contained no executed steps.

## Remaining proof

This run proves provider provisioning, a unique domain, immutable-head
supersession, public readiness, build identity, and production isolation. It
does not yet prove a successful protected manifest callback or authenticated
preview conformance under the corrected controller.

After this evidence PR merges, provider-owned environment removal must be read
back from Railway and attached to issue #424. A later internal PR can complete
the corrected controller and authenticated conformance proof when hosted
Actions execution is available.

Reviewer tokens and Railway credentials never belong in this record, workflow
output, PR comments, or command arguments.
