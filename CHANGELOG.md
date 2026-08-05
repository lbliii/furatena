# Changelog

All notable changes to Furatena are documented here. The project follows
[Semantic Versioning](https://semver.org/) and assembles release notes from
reviewed files in `changelog.d/`.

<!-- towncrier release notes start -->

## [0.1.1] — 2026-08-05

### Added

- Furatena now defines and validates a versioned, gateway-safe MCP Apps metadata contract with explicit capability negotiation, access, redaction, browser security, and non-App fallback rules. ([#331](https://github.com/lbliii/furatena/issues/331))
- Furatena now ships a dependency-free public catalog-search MCP App with accessible ranking, filters, provenance, repair diagnostics, deterministic assets, deny-by-default browser permissions, and structured fallback for non-App hosts. ([#332](https://github.com/lbliii/furatena/issues/332))
- Added request-scoped edition routing with unprefixed latest pages, version-prefixed historical pages, canonical alias redirects, edition-filtered agent endpoints, and matching static exports. ([#351](https://github.com/lbliii/furatena/issues/351))
- Added canonical edition lifecycle metadata, lifecycle-aware retrieval filters and ranking, and non-current edition banners across live, static, PDF, DCP, MCP, search, and agent surfaces. ([#352](https://github.com/lbliii/furatena/issues/352))
- Added Mike-compatible per-mount edition arrays and a mount-keyed `versions.json` hub to live, frozen, static, and agent discovery surfaces. ([#354](https://github.com/lbliii/furatena/issues/354))
- Added lifecycle- and access-aware structural Content IR diffs across documentation editions through matching paginated HTTP and MCP contracts. ([#355](https://github.com/lbliii/furatena/issues/355))
- Added an isolated eight-mount b-stack pilot configuration with bounded public Git tag discovery and reproducible source-provenance evidence. ([#356](https://github.com/lbliii/furatena/issues/356))
- Operators can now produce a deterministic external smoke receipt for every mount and edition in the isolated B-stack pilot. ([#357](https://github.com/lbliii/furatena/issues/357))
- Added strict published-shard and federation-hub contracts with paired semantic and inert presentation objects, content-addressed inventories, bounded verification, compatibility policy, and golden conformance fixtures. ([#359](https://github.com/lbliii/furatena/issues/359))
- Added `fura publish-shard` for deterministic, resumable, manifest-last publication of paired semantic and inert presentation shard objects to S3-compatible storage. ([#360](https://github.com/lbliii/furatena/issues/360))
- Added verified compose-by-reference remote shard mounts with bounded same-origin fetching, atomic mount-local last-known-good generations, request-pinned read surfaces, warm-cache retention for unchanged mounts, lazy per-node presentation and semantic reads, rollback, and zero-local-source catalog serving. ([#361](https://github.com/lbliii/furatena/issues/361))
- Remote federation now routes immutable mount editions before loading them, promotes verified catalogs through cold, warm, and bounded hot tiers, and reports residency churn without retaining the whole composition in memory. ([#362](https://github.com/lbliii/furatena/issues/362))
- Federated remote-shard search now ships deterministic per-shard keyword and TF-IDF indexes, scopes lifecycle and edition before bounded fan-out, and rank-merges results without building a hub-wide corpus index. ([#363](https://github.com/lbliii/furatena/issues/363))
- Furatena now reconciles cross-shard links through a persistent measured delta index, reports broken cross-shard links per mount, and serves sitemap and LLM discovery through lazy-residency-safe per-mount artifacts. ([#364](https://github.com/lbliii/furatena/issues/364))
- Added explicit local-only, commit, and pull-request publication execution with durable provider results, truthful open-review state, and restart-safe reconciliation. ([#383](https://github.com/lbliii/furatena/issues/383))
- Added isolated Git commit and pull-request changesets with exact path validation, deterministic retries, and safe reconciliation of partial or externally changed provider effects. ([#391](https://github.com/lbliii/furatena/issues/391))
- Publication execution can now build verified, content-addressed freeze/export artifacts from clean exact-commit checkouts with provenance, projection privacy scans, and fail-closed readiness reporting. ([#392](https://github.com/lbliii/furatena/issues/392))
- Furatena can now promote one verified publication artifact through preview, staging, and production without rebuilding, with durable approval and policy evidence, serving verification, last-known-good recovery, authorized rollback, and read-only status commands. ([#393](https://github.com/lbliii/furatena/issues/393))
- Added an exact, read-only public projection inspector and one adversarial publishing outcome matrix shared by browser, CLI, MCP, and automation. ([#395](https://github.com/lbliii/furatena/issues/395))
- Added provider-neutral, exact-commit managed-content refresh operations with durable idempotency receipts, service-scoped authorization, verified atomic generations, truthful restart and readiness states, and fail-closed last-known-good rollback. ([#436](https://github.com/lbliii/furatena/issues/436))
- Added a privacy-safe, dependency-gated 90-day Railway template experiment policy with fixed hypotheses, support expectations, provider-aware collection cadence, and deterministic continue, change, stop, or single-variant decisions. ([#460](https://github.com/lbliii/furatena/issues/460))
- Added machine-readable private-image lifecycle records and a public update feed contract with source-bound, digest-pinned promotion, rollback, deprecation, and fail-closed revocation guidance. ([#465](https://github.com/lbliii/furatena/issues/465))
- The live deployment monitor now verifies health, freshness, representative HTML, both agent indexes, complete bulk artifacts, and exact image/content identity while emitting redacted, remediation-ready incident context. ([#466](https://github.com/lbliii/furatena/issues/466))
- Added verified managed-content generation manifests with complete artifact hashes, privacy and representative-surface promotion gates, corruption-aware startup and rollback, and sanitized lifecycle readiness. ([#468](https://github.com/lbliii/furatena/issues/468))
- Added strict, versioned presentation-pack manifests with deterministic layout, skin, and sparse-override resolution, explicit script trust, managed-root containment, and cross-surface provenance. ([#479](https://github.com/lbliii/furatena/issues/479))
- Furatena now ships immutable, complete `docs` and low-asset `vanilla` layouts that render every supported view without project template fixtures while preserving sparse adopter overrides. ([#480](https://github.com/lbliii/furatena/issues/480))
- Furatena now scaffolds manifest-valid presentation packs and provides deterministic check, preview, and cross-surface conformance commands for layout and skin developers. ([#481](https://github.com/lbliii/furatena/issues/481))
- Furatena now ships a provider-neutral preview authorization v1 contract with strict schemas, immutable typed records, canonical fixtures, PKCE and device flows, commit-bound grants, JWKS rollover rules, revocation signals, redaction requirements, and fail-closed compatibility diagnostics. ([#518](https://github.com/lbliii/furatena/issues/518))
- Public-safety proof map documents every delivery surface and its allowed/forbidden audience tests so operators can see where visibility is proven versus gapped. ([#583](https://github.com/lbliii/furatena/issues/583))
- CI now runs a focused public-safety lane that keeps visibility and public-projection audience proofs on the critical path without waiting for full export or release jobs. ([#584](https://github.com/lbliii/furatena/issues/584))

### Changed

- Versioned documentation now emits queryable availability and replacement edges, preserves source provenance while deduplicating semantic content identity, resolves direct pages and declared moves before safe ancestor or landing fallbacks, reports fallback metrics and notices, omits end-of-life targets, and preserves htmx navigation across live, frozen, and static delivery. ([#353](https://github.com/lbliii/furatena/issues/353))
- Furatena's required fast CI now exercises federation artifact publication, S3 storage, and CLI contracts. ([#360](https://github.com/lbliii/furatena/issues/360))
- Added Towncrier release fragments, contributor and security governance, and CI ratchets for silent exceptions and actionable error messages. ([#365](https://github.com/lbliii/furatena/issues/365))
- Author pages now present lifecycle, repository, artifact, and deployment truth independently, with exact identities, server-authorized actions, explicit confirmation, and accessible recovery for dry runs, conflicts, and long-running publication steps. ([#394](https://github.com/lbliii/furatena/issues/394))
- Added a durable, non-secret record of observed Railway PR-preview provisioning, SHA supersession, readiness, production isolation, and remaining conformance work. ([#424](https://github.com/lbliii/furatena/issues/424))
- Added repository-wide steward maps and validation so contributors can find the right invariants, checks, and ownership boundaries before making changes. ([#450](https://github.com/lbliii/furatena/issues/450))
- Private-image candidate checks now smoke the reader, search, catalog, and agent surfaces before an image can be promoted. ([#455](https://github.com/lbliii/furatena/issues/455))
- The Railway template now describes every composer variable, rejects unsafe metadata and networking drift, and documents canonical base-URL handling for Railway-provided and custom domains. ([#456](https://github.com/lbliii/furatena/issues/456))
- Rollback, replacement, and deprecation image targets now require exact stable-release ownership, with non-revocation, registry-availability, and provenance checks wherever the operation requires a deployable image. ([#465](https://github.com/lbliii/furatena/issues/465))
- CI now keeps fast and contract checks on every pull request while routing coverage, browser, packaging, PDF, image, and preview proof only to relevant changes or full-gate events, and production SLO evidence runs every six hours instead of every five minutes. ([#466](https://github.com/lbliii/furatena/issues/466))
- Furatena prepares open-source PyPI Trusted Publishing and treats Railway templates as the hosted live-platform path, so operators can install from the public index after the repository is public and the first GitHub Release succeeds. ([#471](https://github.com/lbliii/furatena/issues/471))
- Managed content now selects one receipt-bound, read-only site generation while resolving platform layouts from the immutable image and runtime state and output from explicit writable roots. ([#478](https://github.com/lbliii/furatena/issues/478))
- The Railway content starter now includes a realistic documentation corpus, true content-only composition through the packaged `docs` layout and Lagoon skin, stable site identity, adopter-owned branding, and guidance for edits, review, exact-commit refresh, rollback, migration, and independent runtime updates. ([#482](https://github.com/lbliii/furatena/issues/482))
- Home-theme conformance now anchors the current governed-documentation title, tagline, and multi-surface value proposition instead of the retired launch slogan. ([#483](https://github.com/lbliii/furatena/issues/483))
- Furatena now brands embedded Pounce startup output with the application name and version while preserving caller-provided Pounce display overrides. `fura serve` also composes one typed contract preflight on stderr, represents stale-freeze recovery without constructor output, leaves readiness logging to Pounce, and reports truthful preflight-only JSON on stdout. ([#486](https://github.com/lbliii/furatena/issues/486))
- Checkout startup now uses `uv run fura serve` consistently, with thin Make and app wrappers plus explicit optional guidance for installing a bare `fura` command. ([#487](https://github.com/lbliii/furatena/issues/487))
- The governed Railway preview guide now distinguishes today's shared-token support from the gated, not-yet-available hosted GitHub migration path. ([#519](https://github.com/lbliii/furatena/issues/519))

### Fixed

- Made private-repository releases use verified checksums and PyPI Trusted Publishing attestations without requiring unavailable GitHub-hosted provenance storage. ([#176](https://github.com/lbliii/furatena/issues/176))
- Edition-switcher conformance now verifies selector markup only on genuinely versioned catalogs while preserving explicit omission coverage for single-channel sites. ([#353](https://github.com/lbliii/furatena/issues/353))
- Railway preview control now fails safely when a PR environment lacks its service and prevents preview operations from changing the production source. ([#424](https://github.com/lbliii/furatena/issues/424))
- The live SLO monitor now parses build identity correctly and preserves routable, redacted receipts when identity checks or bulk-artifact verification fail, time out, cannot start, or return invalid output. ([#466](https://github.com/lbliii/furatena/issues/466))
- Managed-content status and readiness checks no longer remove a live refresh staging workspace; lease-owned reconciliation now preserves concurrent refreshes while reporting staging promptly, and startup, submission, promotion bookkeeping, and rollback share deterministic ownership so live or superseded receipts cannot block future work. ([#468](https://github.com/lbliii/furatena/issues/468))
- Managed-content startup now reports concise, sanitized Railway volume and configuration recovery diagnostics instead of raw Python tracebacks. ([#473](https://github.com/lbliii/furatena/issues/473))
- The Railway private image now runs exactly one Pounce serving process by default, preserving the single-volume content authority while leaving local serving pools automatically sized. ([#475](https://github.com/lbliii/furatena/issues/475))
- `fura --app-root OTHER theme init` now creates the default `theme-skin` scaffold beneath `OTHER` while preserving explicit relative and absolute output paths. ([#481](https://github.com/lbliii/furatena/issues/481))
- Content-only sites now receive the same version navigation and truthful author-status behavior as the built-in application layout. ([#483](https://github.com/lbliii/furatena/issues/483))
- Furatena's startup regression coverage now validates phase-aware preflight output separately from readiness output, preventing legacy test drift from blocking validation. ([#486](https://github.com/lbliii/furatena/issues/486))

### Security

- Railway PR environments now receive fresh sealed reviewer credentials and exact-SHA identity from a trusted, fork-safe default-branch controller before any preview is reported ready. ([#424](https://github.com/lbliii/furatena/issues/424))
- Clean-account Railway conformance now proves anonymous content refresh requests are rejected without changing the active generation. ([#457](https://github.com/lbliii/furatena/issues/457))
- Pull-request image conformance now runs without registry-write, OIDC, or attestation authority, keeping publication identity unavailable to pull-request code. ([#464](https://github.com/lbliii/furatena/issues/464))
- Stable image promotion now requires fresh, successful exact-run evidence for the candidate digest and every smoke and managed-content conformance gate. ([#465](https://github.com/lbliii/furatena/issues/465))
- Railway clean-account conformance now masks generated secrets, retains only whitelisted lifecycle evidence, and records bounded recoverable disposable-project cleanup without publishing raw control-plane identifiers. ([#469](https://github.com/lbliii/furatena/issues/469))
- The proprietary production image now removes the build-only uv executable so its bundled libraries do not expand the deployed vulnerability surface. ([#470](https://github.com/lbliii/furatena/issues/470))
- The Railway image now installs free-threaded Python under `/opt`, runs Furatena as UID/GID 65532, and limits root authority to a validated volume-ownership bootstrap. ([#474](https://github.com/lbliii/furatena/issues/474))
- Furatena now injects CSRF proof headers through both stable and htmx 4 preview request contexts, while normal author reload no longer lets generated cache files restart the Pounce listener. ([#486](https://github.com/lbliii/furatena/issues/486))
- Hosted pull-request previews can now establish commit-bound browser sessions and verify machine Bearer grants locally with strict JWKS freshness, replay protection, revocation, stable denials, and fail-closed outage behavior. ([#517](https://github.com/lbliii/furatena/issues/517))
- Documented the independently operated preview identity broker boundary, least-privilege GitHub policy, privacy retention contract, key recovery model, and general-availability security gates. ([#524](https://github.com/lbliii/furatena/issues/524))
- Preview conformance now rejects cross-origin manifest surfaces and redirects before forwarding a review credential. ([#526](https://github.com/lbliii/furatena/issues/526))
- Public previews remain stateless and return author-only route errors without requiring CSRF session state. ([#544](https://github.com/lbliii/furatena/issues/544))


## [0.1.0] — 2026-07-13

### Added

- Initial alpha packaging, catalog, authoring, agent, deployment, and publication contracts.
