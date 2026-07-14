# htmx 4 preview compatibility report

Issue #328 evaluates htmx 4 without changing Furatena's default browser
runtime. The result is **no-go for making htmx 4 the default**. The pinned lane
is useful and remains in CI, but three browser-contract blockers need upstream
or application fixes before promotion.

The compatibility assumptions follow the official [htmx 4 documentation](https://four.htmx.org/docs/)
for the pinned beta rather than the stable htmx 2 behavior.

## Reversible preview

The only supported preview selector is:

```console
FURA_HTMX_PREVIEW=4.0.0-beta5 make ci-browser-htmx4-preview
```

Any other non-empty value fails application startup. Unset the variable to
roll back. Stable rendering continues to use the existing vendored htmx 2
bundle and attributes.

The preview uses Chirp's `4-preview` provisioning tier and mirrors its exact
beta5 core, htmx-2-compat, and SSE hashes locally. Furatena adds the matching
beta5 preload extension because hover prefetch is part of the app shell:

| Asset | SHA-256 | Bytes | gzip bytes |
| --- | --- | ---: | ---: |
| htmx core | `192d2d425dda6834bd15973a10f55940cea217a3a840f3f819ffd16063be9a68` | 35,985 | 12,824 |
| htmx-2-compat | `7d7fe881d6ae6d4e661b0113e8504bb15acfc1dc1970f07db109bb20c432e53d` | 1,728 | 654 |
| SSE | `fcc844a52779d8450c1c4796feea8d038943f908b9ee974322c276230e6c86cc` | 5,444 | 2,148 |
| preload | `7b08d98f0e2c6981293fc219c68f634caad230dec99dc43c1c74bf1d91534dc5` | 1,678 | 843 |

The four scripts total 44,835 bytes raw and approximately 16,469 bytes gzip.
They are served from `/docs-vendor`, so the preview adds no CDN origin and no
new `script-src` requirement. Chirp's preview policy is emitted as htmx config
metadata before the scripts.

## Browser evidence

The Playwright lane exercises a full page load, hover preload, boosted and
targeted navigation, OOB metadata, history restoration, targeted search, a 404
response, native v4 SSE invalidation, and focus behavior.

| Contract | beta5 result |
| --- | --- |
| Full document load | Pass |
| Hover preload | Pass with `hx-preload` |
| Boosted target content and URL | Pass |
| Targeted search and pushed query URL | Pass |
| History back/refetch | Pass |
| 404 lifecycle and body swap | Pass under the explicit preview policy |
| Native v4 SSE connection and invalidation signal | Pass |
| OOB document title after boosted navigation | **Blocker:** content and URL change, title remains stale |
| Request classification | **Blocker:** targeted boosted request sends `HX-Request-Type: full` |
| Focus during SSE invalidation | **Blocker:** active focus is lost; repeated runs can remove the probe before the reload fetch settles |

The runtime now listens through lifecycle events rather than removed timing
response headers. Repository and route audits found no Furatena use of
`HX-Trigger-After-Swap` or `HX-Trigger-After-Settle`. Chirp also rejects those
headers before send whenever its managed htmx 4 tier is active.

## Baseline and promotion decision

The checked-in stable browser asset reports htmx `2.0.4`, while Chirp's managed
baseline policy and the task premise refer to `2.0.10`. That pre-existing drift
must be reconciled separately so future comparisons start from one declared
baseline; this evaluation does not silently change it.

Promotion requires all of the following:

1. OOB head metadata applies after a main-first v4 swap.
2. Targeted boosted requests are classified consistently by htmx and Chirp.
3. SSE invalidation preserves focus, selection, and deterministic reload ownership.
4. The stable 2.x asset and declared managed baseline agree.
5. The dedicated preview lane passes without compatibility-only event names.

Until then, keep beta5 opt-in, leave GET/browser defaults unchanged, and roll
back by unsetting `FURA_HTMX_PREVIEW`.
