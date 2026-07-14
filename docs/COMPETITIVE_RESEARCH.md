# Competitive research: developer documentation platforms

Research snapshot: **2026-07-13**.

This report analyzes 28 first-party blog posts, migration accounts, customer case
studies, and official product references. It focuses on the market around Furatena:
hosted documentation platforms, open-source documentation frameworks, custom docs
stacks, API developer portals, and agent-facing documentation infrastructure.

## Bottom line

The market has moved beyond “beautiful documentation from Markdown.” That promise is
now table stakes. Hosted vendors compete on removing maintenance, accelerating previews,
supporting non-engineering contributors, generating API references and SDKs, and making
content directly consumable by coding agents. Open-source frameworks compete on control,
extensibility, static delivery, and ecosystem familiarity.

Furatena's strongest defensible position is the combination those products rarely offer
together, but the current product should be described more narrowly than the target
platform:

> **An open, local-first documentation compiler and runtime that turns mixed technical
> sources into a validated graph and coordinated web, static, search, reference, and
> agent artifacts.**

That statement is supportable today. A complete governed author-to-production platform
is not. The implementation is broad and real, but Furatena is still an unreleased alpha
with a narrow Python runtime, local-only authoring safety assumptions, a frozen preview
deployment, and publication-control work still in open pull requests. The immediate
strategic job is therefore productization and proof, not adding another layer of breadth.

## Method

The evidence set is intentionally weighted toward accounts that explain an actual
decision: what a team replaced, what alternatives it considered, what broke, what it
selected, and what it still could not do.

Source strength is classified as:

- **A — customer-authored:** a team describes its own docs rebuild or migration.
- **B — vendor case study:** the vendor reports a customer's migration and metrics;
  useful, but the numbers are not independently verified here.
- **C — vendor product/engineering:** primary evidence of product direction and
  positioning, not neutral evidence that the capability performs as claimed.
- **D — official ecosystem reference:** primary evidence of a framework's supported
  model or outputs.

The set is directional rather than statistically representative. It overrepresents
developer-tool and API companies that publish engineering or DevRel blogs. Vendor
metrics are always attributed to the vendor source.

## Current project-state audit

This section grounds the market analysis in the implementation rather than the README
alone. The audit inspected the CLI, application routes, package metadata, DCP and RBAC
contracts, authoring and deployment documentation, committed artifacts, benchmarks,
migration and platform pilots, CI workflows, tests, open issues and pull requests, and
the latest release run.

Audit basis: local commit `d172d6659cc0046b02c1129c1f34580b911d4380` on
`codex/issue-350-immutable-edition-shards`, with uncommitted edition-shard work present.
Branch-only or uncommitted behavior is not counted as released. The latest observed
successful `main` validation/deploy was commit `8c5ab738`; there is no GitHub release.

### State verdict

Furatena is an **advanced alpha documentation compiler/runtime and machine-readable
catalog**, not yet a turnkey documentation platform. Its technical center of gravity is
substantially stronger than a normal static-site generator: typed graph construction,
mixed-format ingestion, validation, access filtering, coordinated artifacts, reference
resolution, deterministic agent evaluation, and API-spec projection are implemented and
tested. Its product shell is much less mature: installation is restrictive, there is no
published release, remote collaborative authoring is deliberately constrained, the live
deployment is a frozen single-replica preview, and the production publication saga is
not complete.

### Capability truth table

| Capability | Current state | Evidence and boundary |
|---|---|---|
| Mixed-format compiler and DCP graph | **Implemented** | Markdown, HTML, RST, MyST, MDX lowering, Python autodoc, OpenAPI projections, typed edges, Content IR, and JSON graph contracts exist. Format fidelity varies; MDX and Sphinx extensions can require manual work. |
| Static documentation release | **Implemented and deployed** | The committed public build contains 257 catalog pages, 2,063 edges, 58 declared routes, and 1,060 static artifacts, including HTML, Markdown and text sidecars, search, graph, inventory, and agent manifests. |
| Dynamic documentation application | **Implemented; limited production proof** | Search, query, retrieval, content negotiation, health endpoints, and author routes exist. The Railway proof runs a frozen catalog in one replica; it does not continuously sync content. |
| Browser authoring | **Implemented for local/trusted use** | Dashboard, studio, source, validation, save, create, and lifecycle actions exist. Remote author serving is intentionally restricted without a trusted gateway identity integration. It is not a collaborative visual CMS. |
| Agent-readable outputs | **Implemented and deployed** | `llms.txt`, full corpus, catalog, semantic and structure indexes, per-page Markdown, a tool manifest, and six declared read tools are exported. |
| MCP | **Implemented as a local process** | `fura mcp` supports read workflows and guarded author mutations. The deployed Railway web service does not expose an MCP transport; its `tools.json` is discovery, not a remote MCP endpoint. |
| Access policy and isolation | **Implemented in the core** | Anonymous/reader/contributor/publisher/admin roles, mount/page policies, team filters, and public-output filtering share a policy boundary. A packaged enterprise IdP/gateway integration is not present. |
| Migration diagnostics | **Implemented with real pilot evidence** | The recorded pilots cover 981 files with 91.5% clean first pass. Only 302 of 931 MDX files were safe reversible conversions; 629 required manual treatment, and Sphinx extension gaps produced many warnings. |
| OpenAPI reference and API diff | **Implemented at meaningful pilot scale** | A fixed GitHub REST corpus produced roughly 1,200 pages/operations, examples, schemas, and a breaking-change report. SDK generation is not part of the product. |
| Retrieval quality and evaluation | **Implemented; evidence set is small** | Versioned known-answer data, access/edition/mount/tag filters, latency and quality reports exist. The current retrieval baseline has only six positive cases; reranked-hybrid MRR is 0.708 and recall@3 is 0.667. |
| PDF | **Implementation present; product channel incomplete** | The CLI and rendering dependencies/tests exist, but the committed dogfood channel manifest still marks the PDF bundle as planned. Do not lead with seamless multi-channel PDF delivery yet. |
| Themes and frontend customization | **Implemented foundation** | Theme entry points, the Lagoon pack, tokens, view overrides, linting, and scaffolding exist. The ecosystem and polished template breadth trail Docusaurus, Starlight, Mintlify, GitBook, and Fern. |
| Governed author-to-production publishing | **Contracts implemented; workflow incomplete** | Plan, state, decision, event, capability, approval, and provider protocols exist. Workflow and approval implementations are in open PRs; the concrete isolated Git changeset, immutable artifact promotion, rollback, and cross-surface conformance work remain open. |
| Release and installation | **Release candidate prepared; external proof pending** | Package metadata is prepared for `0.1.1` and remains Alpha, requires CPython `>=3.14,<3.15`, and operationally requires free-threaded 3.14 with the GIL disabled. The `v0.1.0` workflow proved the build but failed on GitHub-hosted provenance storage that is unavailable to user-owned private repositories; `0.1.1` uses checksums plus PyPI Trusted Publishing attestations and still requires a successful protected release before this becomes product-ready. |
| Runtime operations | **Proof deployment, not managed service** | Health and readiness contracts, artifact audits, deployment profiles, and CI lanes exist. There is no hosted control plane, runtime content resync, multi-replica proof, customer tenancy, backup/restore product, or published SLO. |

### Verification result

The local `make ci-fast` lane passed: formatting, lint, owned type checks, and the selected
core test suite all succeeded. The repository currently contains 212 Python source files,
106 test modules, and 767 directly declared test functions. This is credible engineering
depth, though test volume should not be confused with customer adoption or operating
proof. The type audit also reports 338 repo-wide diagnostics against a 336 baseline;
owned ratchets passed, while one non-owned broad-drift module remains report-only.

The agent lane also passed with zero findings and 15 selected agent/MCP/evaluation tests.
The content and route contract lane passed its gates and 98 selected tests, but `fura
check` reported 13 warnings and 11 informational findings: eight stale or missing frozen
outputs, two explicitly trusted template-renderer boundaries, three apparently unused
template partials, and several routes not referenced from templates. These are not test
failures, but they reinforce that the checked-in public build is not a clean release
candidate at this snapshot.

### Where Furatena is positioned now

Furatena currently sits **above a conventional open-source docs framework but below a
hosted documentation platform**:

- Versus Docusaurus, Starlight, Hugo, and similar frameworks, it offers a richer typed
  corpus, coordinated human/agent artifacts, access-aware retrieval, cross-reference
  inventories, migration diagnostics, and static/live parity. It trails them in runtime
  accessibility, ecosystem size, theme polish, contributor familiarity, and low-risk
  installation.
- Versus Mintlify, GitBook, ReadMe, and Fern, it offers stronger inspectability,
  deployment independence, offline/static artifacts, and policy-aware machine contracts.
  It trails them in activation, managed hosting, collaborative editing, preview services,
  analytics, customer support, enterprise identity packaging, and operational proof.
- Versus Fern and SDK-oriented portals, it is complementary rather than substitutive:
  Furatena can govern and connect prose, references, releases, and agent outputs, but it
  does not generate multi-language SDKs.
- Versus MyST and Sphinx, it brings a more application-like web and agent layer, while
  trailing their mature scientific publishing semantics and ecosystem.

The best near-term category is therefore **documentation build and governance
infrastructure for platform teams**, with migration and agent-safe artifacts as the
wedge. “Documentation platform” is a credible destination, but overstates the current
delivery and collaboration layer.

## Executive findings

### 1. Avoiding platform maintenance is the strongest hosted-platform buying trigger

HubSpot said it had become an “accidental docs platform team” and moved to Mintlify so
engineering could focus on developer experience. balena reached the same conclusion
after maintaining its own framework and repeatedly having to justify work on search and
an API playground. Unleash's Fern case study says a one-person documentation team was
maintaining Docusaurus plugins and custom components instead of content.

This is the central objection Furatena must answer. Local ownership is attractive only
if installation, upgrades, previews, search, hosting, and incident handling feel bounded.
The sales story needs measured operational cost, not only architectural elegance.

### 2. Repo ownership and Markdown portability are important, but no longer unique

Trophy explicitly kept OpenAPI as the portable source of truth so it could move API docs
from Fern to Mintlify. balena retained GitHub ownership while adopting GitBook. Fern,
Mintlify, ReadMe, GitBook, and Postman's Fern integration all advertise Git-backed
workflows or repositories.

“Your content stays in Git” is therefore a requirement, not a differentiator. Furatena
should lead with what its corpus can become and how its contracts remain inspectable,
portable, and independently deployable.

### 3. Clean Markdown, `llms.txt`, AI search, and MCP are becoming table stakes

Mintlify reported that identified coding agents generated 45.3% of requests across its
hosted docs during a 30-day March 2026 sample. The result is vendor-supplied and specific
to Mintlify's customer base, but it explains the speed of the market response. Mintlify,
Fern, GitBook, and ReadMe now promote Markdown representations, `llms.txt`, MCP, AI
search, agent analytics, or combinations of them. Customer posts from HubSpot, Coinbase,
balena, Column, and SuperTokens describe agent consumption as a first-class requirement.

Furatena cannot differentiate on “agents can read the docs.” Its stronger claim is that
agents can retrieve typed, filtered, edition-aware graph data with explicit stale/private
safety and can use the same reviewed release artifacts as human readers.

### 4. Migration is both the wedge and the hidden cost center

Cloudflare changed 8,060 files in its Hugo-to-Astro/Starlight migration, spent six weeks
on planning and execution, and used Markdown AST transforms to keep the final code freeze
to eight hours. ReadMe's migration guidance emphasizes that stale content, redirects,
custom components, versioning, and stakeholder agreement on information architecture are
harder than copying files. Fern's customer-call synthesis says smooth migration is a
requirement because prospects often have content split across several platforms and a
fixed launch deadline.

Furatena's structured migration report, safe MDX lowering, mixed-format adapters, owner
assignment, link validation, and clean-page scoring form a concrete product wedge. The
existing public-pilot evidence—981 sources, 91.5% clean on the first pass—should be part
of the product narrative rather than hidden in an internal design note.

### 5. Information architecture is a product problem, not a renderer feature

LocalStack's rebuild centered on task-based navigation across multiple cloud products.
SuperTokens separated guides from references and reorganized tutorials around the real
authentication journey. Coinbase observed developers getting lost before it consolidated
API references and simplified navigation. ReadMe warns that multi-product migrations
accumulate information-architecture debt that an importer cannot resolve.

Furatena's graph and persona/Diátaxis work are relevant, but they need user-facing tools:
orphan detection, duplicate-concept signals, navigation-depth diagnostics, task-journey
checks, and migration recommendations that explain why a structure is hard to use.

### 6. A single technical source increasingly drives multiple developer surfaces

Column now generates API reference from the same OpenAPI artifact used by its server and
publishes each page as HTML and clean Markdown. Payabli's Fern case study describes one
spec driving API reference, 385 reused examples, and eight SDKs. Trophy uses OpenAPI as
the neutral source for both docs and SDK tooling. MyST demonstrates the adjacent demand
for one corpus to produce HTML, PDF, Word, LaTeX, JATS, and structured JSON.

Fern is strongest when “multiple surfaces” means docs plus generated SDKs. Furatena is
better positioned when it means live and static sites, PDF/document outputs, graph data,
search artifacts, references, and agent interfaces. Furatena should integrate with SDK
generators rather than imply it replaces them.

### 7. Custom control remains a reason to reject packaged frameworks

Tinybird chose a custom Next.js and Markdoc stack over Docusaurus or Starlight because it
wanted stack familiarity, deep styling control, authentication, and future personalized
documentation. Cloudflare stayed fully open source because contributor transparency and
framework control were strategic. Payabli reportedly left Mintlify after platform updates
overrode custom styling. Deepgram reportedly left ReadMe partly because reusable custom
components and raw-code workflows were difficult.

Furatena should preserve escape hatches, theme packaging, stable HTML boundaries, and
deployment choice. A “managed feel without managed lock-in” is more compelling than
maximal customization by itself.

### 8. Fast preview and contribution workflows directly affect freshness

Coinbase reported reducing preview creation from roughly 30 minutes to under one minute
and generating a preview per pull request after adopting Mintlify. Deepgram's Fern case
study reports cutting PR turnaround from 30–60 minutes to 5–6 minutes and increasing the
number of contributors. GitBook and Fern both position visual or assisted editing as a
way to let product managers and writers contribute while retaining reviewable Git flows.

Furatena's instant local author loop is valuable, but buyer-facing proof should include
clone-to-edit time, preview deployment, review behavior, and the path for contributors
who do not want to install Python or use a terminal.

## Evidence matrix

| # | Source | Strength | Change or subject | Decision signal | Furatena implication |
|---:|---|:---:|---|---|---|
| 1 | [Trophy: How We Built Our Developer Docs With Mintlify & Fern](https://trophy.so/blog/how-we-built-our-developer-docs-with-mintlify-fern) (2025-05-15) | A | Evaluated GitBook, Docusaurus, Hashnode, Fern, and Mintlify; kept Fern for SDKs and chose Mintlify for docs | Writing experience, React components, custom-domain cost, OpenAPI portability; about one week to update | OpenAPI portability and pleasant authoring are baseline. A small team can switch quickly when content is portable. |
| 2 | [HubSpot: Our Mintlify Migration Story](https://developers.hubspot.com/blog/optimizing-developer-docs-in-the-age-of-ai-our-mintlify-migration-story) (2025-10-09) | A | Homegrown platform to Mintlify plus a purpose-built MCP layer | Stop maintaining infrastructure; gain auth, permissions, search, AI chat, and reliable Git publishing | Furatena must prove low operations and distinguish safe context selection from generic MCP exposure. |
| 3 | [balena: docs platform migration to GitBook](https://blog.balena.io/balena-docs-platform-migration-to-gitbook/) (2026) | A | Homegrown framework to GitBook | Better search, AI assistant, MCP, OpenAPI playground, vendor support, GitHub ownership | Hosted platforms can preserve repo control. Furatena's advantage must be deployment and contract control, not Git alone. |
| 4 | [Cloudflare: Upgrading our developer documentation](https://blog.cloudflare.com/open-source-all-the-way-down-upgrading-our-developer-documentation/) (2025-01-08) | A | Hugo to Astro/Starlight; 8,060 files changed | Open-source values, extensibility, contributor DX, content collections, performance; six-week migration | Large open-source teams accept framework ownership when transparency and extensibility are strategic. Migration automation is critical. |
| 5 | [Tinybird: We rebuilt our docs from scratch](https://www.tinybird.co/blog/new-docs) (2025-04-24) | A | Sphinx to custom Next.js, Markdoc, Pagefind stack | Auth and personalization, styling flexibility, contributor workflow, stack familiarity, static reliability | Strong evidence for customizable, repo-owned deployments and against rigid docs-specific frameworks. |
| 6 | [LocalStack: Docs v2](https://blog.localstack.cloud/announcing-localstack-docs-v2/) (2025-07-10) | A | Rebuilt on Astro/Starlight | Multi-product information architecture, task-based navigation, modular growth, clearer onboarding | Catalog structure and task-oriented diagnostics can be a Furatena differentiator if surfaced as product UX. |
| 7 | [Column: Rebuilding Column's Documentation](https://column.com/blog/rebuilding-columns-documentation/) (2026-06-18) | A | Separate Markdown and spec to spec-generated Fumadocs site | Eliminate API drift; static search and HTML; clean Markdown per page; agent handoff; future incremental builds | Very close to Furatena's dual-output thesis. Furatena needs a sharper story around graph, governance, federation, and release channels. |
| 8 | [Coinbase: Lessons from AI Docs & Mintlify](https://www.coinbase.com/en-de/blog/Coinbase-PM-Blog-AI-Docs-and-Mintlify) (2025-11-03) | A | CDP docs overhaul with Mintlify | IA research, machine readability, previews under one minute, PR preview links, six-week launch | Preview speed and user-tested IA are measurable buyer outcomes. |
| 9 | [SuperTokens: Rethinking our documentation](https://supertokens.com/blog/rethinking-documentation) (2025-07-04) | A | Reorganized and standardized a growing docs corpus | Task journey, guides/reference separation, Vale rules, faster quickstarts, `llms.txt` | Quality governance and journey-aware structure matter independently of the renderer. |
| 10 | [Endor: Rebuilding our documentation site using AI](https://endor.dev/blog/rebuilding-our-docs) (2025-12-05) | A | AI-assisted rewrite in two days rather than an estimated week | User research and IA before generation; humans wrote the highest-leverage pages | Automated writing is not enough. Furatena should assist review, provenance, and consistency without promising autonomous quality. |
| 11 | [wp-content.io: Explore the brand new docs](https://wp-content.io/blog/2025/06/11/explore-the-brand-new-docs/) (2025-06-11) | A | MkDocs plus Postman to Mintlify | Unify fragmented tools; improve maintenance, quickstart, guides, API reference, and navigation | Tool consolidation is valuable. Furatena should demonstrate how prose and API artifacts coexist without separate portals. |
| 12 | [HMPL.js: Revamped docs with Astro Starlight](https://dev.to/hmpljs/new-docs-how-we-revamped-hmpl-documentation-with-astro-starlight-19ce) (2025-07-22) | A | VuePress to Astro/Starlight | Modern design, ecosystem freshness, Markdown compatibility, built-in components | Open-source alternatives can provide polished defaults with low migration friction. Theme polish remains competitive. |
| 13 | [Presslabs: Rebuilt documentation using Hugo](https://www.presslabs.com/how-to/documentation-hugo/) (updated 2019-05-31) | A | MkDocs to Hugo | Static speed, security, simple hosting, Git versioning; accepted loss of dynamic editing | Historical baseline for the static-value proposition; Furatena can retain those benefits while adding live/agent capabilities. |
| 14 | [Aporia: Releases New Docs](https://www.aporia.com/blog/aporias-new-documentation/) (2023-01-12) | A | Ground-up docs stack revision | Flexibility, security, iteration speed, clearer navigation and educational content | Shows pre-agent buying criteria that remain relevant; AI features have layered on top rather than replaced them. |
| 15 | [Fern case study: Unleash](https://buildwithfern.com/customers/unleash) (2026) | B | Docusaurus and Kapa to Fern; 300+ pages | Vendor reports two-week solo migration, less component upkeep, visual editing into Git, agent-ready Markdown, 91.5% AI-search resolution | Direct threat to Furatena's migration and agent-readiness story; neutralize with public reproducible evidence and deployment freedom. |
| 16 | [Fern case study: Payabli](https://buildwithfern.com/customers/payabli) (2026) | B | Mintlify to Fern in three weeks | Styling control, one spec for docs/examples/eight SDKs, custom components, agent-specific inclusions | Fern owns the docs-plus-SDK consolidation story. Furatena should interoperate and emphasize broader publishing and governance. |
| 17 | [Fern case study: Deepgram](https://buildwithfern.com/customers/deepgram) (2026) | B | ReadMe to Fern | AsyncAPI plus OpenAPI, raw-code workflow, custom components, broader authorship | Protocol breadth and authoring flexibility influence API portal switches. |
| 18 | [Fern: customer-call synthesis](https://buildwithfern.com/post/building-fern-site) (2026-06) | C | Consolidated themes from months of prospect calls | Unified docs/SDKs, migration, self-hosting, SSO/RBAC, AI control, governance, enterprise polish | Useful direct market map. Furatena aligns with migration and self-hosting but needs a clear enterprise packaging story. |
| 19 | [Fern: largest docs sites render 6.4x faster](https://buildwithfern.com/post/faster-docs) (2026-07-02) | C | Replaced monolithic site blobs with content-addressed pieces | Large sites exposed slow publish/load, cache staleness, outages, and lack of history | Performance at 2,000 pages, 50 API versions, and 35,000 routes is a competitive contract, not an implementation detail. |
| 20 | [Mintlify: Docs on autopilot](https://www.mintlify.com/blog/docs-on-autopilot) (2026-04-03) | C | Repo-to-doc generation plus PR-triggered maintenance automation | Cold start, drift detection, generated changelogs, scheduled audits, docs drafts | Strong threat for solo founders. Furatena should not compete on effortless generation until it can prove equivalent activation. |
| 21 | [Mintlify: State of agent traffic](https://www.mintlify.com/blog/state-of-ai) (2026-04-03) | C | Analysis of 790M requests over 30 days | Reports 45.3% identified agent traffic and 45.8% browser traffic; notes user-agent limitations | Confirms agent delivery is central, while also showing the need for independent, reproducible measurement. |
| 22 | [Mintlify: Documentation is your AI interface](https://www.mintlify.com/blog/docs-as-ai-interface) (2026-03-13) | C | Agent-facing docs stack model | Clean Markdown, `llms.txt`, MCP, emerging `skill.md`, agent analytics | These outputs are becoming category conventions. Furatena should lead with richer contracts and policy, not file presence. |
| 23 | [Mintlify: Building an LSP for your docs](https://www.mintlify.com/blog/building-an-lsp-for-your-docs-by-overcoming-vercel) (2025-10-23) | C | Type-aware code examples through Twoslash in a multi-tenant platform | IDE-like documentation UX and typed examples | Interactive code intelligence is a visible product-quality benchmark Furatena does not currently foreground. |
| 24 | [GitBook: MCP and documentation](https://www.gitbook.com/blog/what-is-mcp-server-documentation) (2026-05-08) | C | Automatic MCP exposure for published docs | One source for web, SEO, and agents; analytics; no customer-operated server | Generic MCP exposure is commoditized. Furatena's opportunity is inspectable tools, filters, editions, and access boundaries. |
| 25 | [ReadMe: Docs Audit](https://readme.com/blog/improve-documentation-docs-audit) (2026-01-21) | C | Site-wide AI quality and style analysis | Stale content, broken links, terminology, structure, scheduled/exportable results | Furatena's deterministic checks are strong but need comparable summaries, trends, and manager-facing remediation views. |
| 26 | [ReadMe: Enterprise documentation migration](https://readme.com/blog/enterprise-documentation-migration) (2026-06) | C | Importer, Git import, or professional migration | IA, redirects, stale content, custom components, versioning, stakeholder alignment, AI readiness | Validates migration as a service and workflow, not merely a parser. Redirect and IA planning deserve first-class reports. |
| 27 | [Postman: Document a collection with Fern](https://learning.postman.com/latest-v-12/docs/fern/document-a-collection) (2026) | D | Postman collection to Fern-generated docs and customer-owned GitHub repo | One-click publication, brand extraction, Git ownership, guides beside API reference | Distribution partnerships can erase setup friction. Furatena needs equally concrete import recipes and ecosystem integrations. |
| 28 | [MyST: ecosystem and output model](https://mystmd.org/guide) (current) | D | Markdown/notebooks to AST, HTML, PDF, LaTeX, Word, JATS, and JSON | Rich technical semantics and multi-format publishing from one source | Closest adjacent proof for multi-output demand. Furatena should preserve compatibility and differentiate on live hypermedia, governance, and agents. |

## Competitive map

| Segment | Representative products | Best current story | Where Furatena is exposed | Where Furatena can win |
|---|---|---|---|---|
| Hosted docs platforms | Mintlify, GitBook, ReadMe | Launch quickly; polished defaults; search/AI/MCP; previews; visual collaboration; vendor-operated hosting | Furatena is currently infrastructure the buyer must install and operate; it lacks a production collaboration/control plane | Self-hosting, static independence, inspectable contracts, local/offline work, one source for more outputs |
| API developer-experience suites | Fern, ReadMe, Redocly, Stainless-adjacent workflows | Spec-driven API reference, SDKs, examples, interactive playgrounds, enterprise services | Furatena does not own SDK generation and has less visible API-playground polish | Federated prose/reference graph, deployment control, mixed-format migration, release shards, integration with rather than replacement of SDK tools |
| Open-source docs frameworks | Docusaurus, Astro/Starlight, VitePress, Hugo, Next.js/Fumadocs | Free, extensible, static, familiar ecosystems, strong theme/component communities | Larger ecosystems, supported runtimes, easier installation, and more recognizable frontend stacks | Local authoring without full rebuilds, graph/query contracts, frozen/live parity, migration diagnostics, and agent-safe data |
| Technical publishing | Sphinx, MyST/Jupyter Book | Cross-references, scientific semantics, executable content, PDF/book/Word outputs | More mature scientific and publication semantics | Modern app UX, migration diagnostics, federated live graph, agent retrieval, multiple deployment profiles |
| Agent overlays | Kapa, built-in vendor assistants, generic RAG/MCP | Add search/chat quickly and measure questions | Furatena's current deployed MCP transport is not yet the default web endpoint | Source-aware typed retrieval, filters, private/stale/version policy, reproducible offline evaluation |

## Positioning recommendation

### Lead message for the current product

> **Furatena is the content control plane for human- and agent-facing technical
> documentation.** It turns mixed, repo-owned technical sources into one governed graph
> plus coordinated web, static, search, reference, and agent outputs.

This message matches the implementation without implying managed hosting, remote
collaboration, continuous content synchronization, or a complete publication control
plane.

### Target message after the delivery layer ships

> **Ship one reviewed documentation corpus everywhere.** Author locally or through a
> trusted workflow, approve one immutable revision, and promote the same verified
> artifacts to human and agent channels without hosted-platform lock-in.

Use this stronger message only after the concrete Git provider, immutable artifact
promotion, approval flow, rollback, remote identity, and cross-surface conformance work
is complete and demonstrated outside the dogfood repository.

### Supporting proof

1. **Know migration risk before changing source files.** Show the structured migration
   report, safe conversions, unsupported construct families, broken links, owners, and
   first-pass clean-page rate.
2. **Choose static or live without forking content.** Demonstrate GitHub Pages and a live
   service produced from the same commit, with explicit capability differences.
3. **Give agents governed context, not a website scrape.** Demonstrate edition, mount,
   tag, URL, and access filters; typed edges; stale/private warnings; and deterministic
   retrieval evaluation.
4. **Keep build artifacts independently usable.** Show HTML, Markdown sidecars,
   `catalog.json`, `semantic.json`, `structure.json`, `objects.inv`, `tools.json`, and
   static search from one freeze.
5. **Make alpha status and boundaries explicit.** State the supported runtime, local
   authoring trust model, frozen-versus-live behavior, deployed MCP boundary, and which
   publication capabilities remain experimental.

### Messages not to lead with

- “Your docs are Markdown.” Every segment supports Markdown or imports it.
- “Your content lives in Git.” Hosted competitors increasingly support Git ownership or
  synchronization.
- “We generate `llms.txt`.” That is already expected.
- “We have MCP.” Generic read-only MCP is becoming automatic on hosted platforms.
- “No JavaScript.” This matters only when connected to lower operations, security,
  accessibility, or contributor outcomes.

## Recommended product and go-to-market priorities

### P0: turn the implementation into an adoptable product

1. Publish a real installable release. Fix the private-repository attestation path,
   complete isolated build verification, publish the package, and prove install/upgrade/
   rollback from outside this checkout.
2. Reduce runtime friction. CPython 3.14t with the GIL disabled is a severe adoption
   constraint; either support a mainstream Python runtime or package an installation
   path that makes the unusual runtime invisible and supportable.
3. Finish one honest production path before expanding scope: trusted identity, concrete
   Git changesets, immutable artifact creation, approval, promotion, verification, and
   rollback of the same bytes.
4. Decide whether remote MCP is a product surface. If yes, deploy and secure it with the
   same access policy, identity, rate limits, observability, and version contracts as the
   web application. If no, position MCP as local/CI tooling and make static agent
   artifacts the deployment story.
5. Validate a clean external repository, not only dogfood and fixed pilots. Measure time
   to install, first preview, first migration report, first static release, upgrade, and
   recovery; record every undocumented dependency.

### P1: make existing differentiation legible

1. Build a public, repeatable migration demo from MDX and Sphinx/MyST corpora. Publish
   the input revision, elapsed time, clean-page rate, remaining blockers, and remediation
   grouping; do not hide the 629 manual MDX cases.
2. Publish a static-versus-frozen-runtime demo from one commit. Make shared artifacts,
   runtime-only query/retrieval, and the lack of continuous runtime sync explicit.
3. Turn agent safety into an interactive proof: current versus stale edition, public
   versus private content, and a retrieval answer that cites the exact source node.
4. Put the author loop on a stopwatch: install, import, serve, edit, validate, preview,
   freeze, and deploy. Compare new-site and imported-site journeys separately.
5. Expand the known-answer retrieval set beyond six positive cases and publish failure
   analysis. Current MRR/recall evidence is useful engineering telemetry, not yet a
   market-grade quality claim.

### P2: close workflow objections and integrate selectively

1. Provide a turnkey pull-request preview recipe and document preview creation time.
2. Make migration redirects, top-URL preservation, duplicate content, orphans, and nav
   depth visible in one report.
3. Add manager-facing trends to deterministic checks: score over time, new versus
   existing findings, owner, severity, due state, and exportable evidence.
4. Package deployment so a buyer can choose local, static, managed cloud, or self-hosted
   live service without learning internal architecture.
5. Decide and state the non-terminal contributor path. It can be a constrained editor,
   Git provider workflow, or integration; ambiguity loses to GitBook, Fern, and ReadMe.
6. Integrate OpenAPI and AsyncAPI references into the catalog while treating established
   SDK generators as partners or upstream producers.
7. Preserve MyST/Sphinx cross-reference and multi-output compatibility rather than
   recreating every scientific authoring feature.
8. Add agent-traffic and retrieval-quality measurements with clear privacy boundaries;
   avoid vanity page-view analogues.
9. Explore typed or executable code examples only after migration, reliability, and
   deployment workflows are effortless.

## Best initial pilot profiles

### Strong fit

- A platform-docs team migrating a mixed MDX, Markdown, RST, or MyST corpus that must
  preserve static output while adding search and agent access.
- A security-conscious platform team evaluating self-hosting, explicit public/private
  boundaries, and audit-ready machine-readable artifacts, with the understanding that
  the complete immutable publication workflow is still being built.
- A documentation team producing several outputs—web, PDF or document exports,
  inventories, agent context, and searchable references—from overlapping content.
- An open-source or infrastructure project that values repo and framework transparency
  but has outgrown a conventional static build loop.

### Weak fit unless the product changes

- A solo founder whose primary need is a beautiful hosted site in five minutes with no
  infrastructure decisions. Mintlify and GitBook are optimized for this job.
- An API-first company whose decisive requirement is generated SDKs in many languages.
  Fern and dedicated SDK platforms have the clearer offer.
- A predominantly non-technical documentation team that requires a polished visual CMS
  and real-time co-editing.
- A team seeking only AI chat over an existing site. An overlay is easier to buy.

## Claims to validate next

The desk research identifies promising hypotheses but does not establish willingness to
adopt or pay. The next research round should test these claims with real corpora:

1. Does a structured pre-migration report reduce perceived switching risk enough to
   start a pilot?
2. Which buyer values dual static/live publication, and which sees it as complexity?
3. Will security and platform teams pay for agent access boundaries and immutable
   evidence, or do they expect them from an existing docs vendor?
4. Is PDF/document output an active requirement in the target market or mainly adjacent
   scientific-publishing demand?
5. What maximum setup and upgrade burden is acceptable before “repo-owned” becomes
   “self-operated overhead”?
6. Is a visual editor required for adoption, or can Git-provider edits and preview links
   cover the relevant non-terminal contributors?
7. Do buyers want Furatena to render API references directly, or to index and govern
   outputs from Fern, Redocly, Stainless, or other generators?

For interviews, use the existing activation protocol and migration pilots. Ask each
participant to bring a real repository, choose a deployment profile, interpret the
migration findings, publish one channel, and retrieve one known answer through an agent.
Record where they hesitate; those pauses will be more useful than feature preference
questions.
