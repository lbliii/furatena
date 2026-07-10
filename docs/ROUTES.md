# Route manifest

Furatena publishes the application-owned route contract at `GET /routes.json`.
The manifest is also included in static exports. Each entry records the path,
route name, accepted methods, owning mount, handler and Python origin, response
contract, template, and fragment metadata.

`tests/fixtures/route_manifest.snapshot` is the compact structural baseline.
`tests/fixtures/route_behavior_snapshot.json` records representative successful
and error responses. Refactors that change registration or observable behavior
must update the implementation and snapshots together, making route drift an
explicit review decision.

The contract test runs on free-threaded CPython with `PYTHON_GIL=0` as part of
the normal test lane. The manifest only inspects immutable registration metadata
after app construction; it does not add shared mutable runtime state.

## Runtime transition evidence

`tests/test_chirp_docs_response_conformance.py` complements the structural
manifest with Chirp's `RouteSmokeCase` and compiled-transition trace helpers.
The contract lane exercises representative document, search, author, and error
recovery routes as full-page, boosted, and narrow-target requests. It preserves
the Furatena-specific response marker assertions while also pinning the stable
compiled route and template/block transition identities returned by the frozen
application.

When a smoke assertion fails, its message includes the route, request mode,
target, expected template and block, response status, and observed render shape.
This makes full-document leakage into an HTMX target diagnosable from the CI log.
For deeper inspection, run the focused test with the same free-threaded posture:

```console
PYTHON_GIL=0 uv run pytest -q -vv \
  tests/test_chirp_docs_response_conformance.py \
  -k 'route_smoke or transition_evidence'
```

In debug mode, typed Chirp responses carry a bounded
`X-Chirp-Return-Trace` header. `transition_observation()` decodes one response;
`transition_coverage()` reports missing request modes or compiled transition
IDs. OOB responses may intentionally have no template transition, but still
must expose their stable route identity and `boosted`, `targeted`, or `oob`
mode tags. An unmatched 404 has no compiled route identity, so the error-recovery
transition assertion uses the typed `/errors/suggest` fragment while the
existing 404 full-page and boosted response-shape tests remain authoritative.

Route definitions are grouped in `furatena.catalog.route_registrars` by public,
author, search, catalog/export, media, and error surfaces. `DocsApp` composes
those registrars with its dynamic mount and localized routes before app freeze.
Handlers that require direct source inspection, such as the Open Graph image
route, are module-level functions.

Dynamic content routes also expose stable machine-readable aliases without
duplicating registrations in the structural manifest. A page at `/docs/page/`
is available as markdown at both `/docs/page.md` and `/docs/page/index.md`;
static export writes the same aliases alongside `index.html`. Behavioral
contracts cover those aliases and their `text/markdown` response type.
The dogfood manifest includes the concrete top-level aliases `GET /docs.md`,
`GET /releases.md`, and `GET /shared.md`; nested content is served through the
same mount handlers at both extension and adjacent `index.md` forms.
Live page GET routes also negotiate the same representation when an explicit
`Accept: text/markdown` preference outranks `text/html`. Negotiated markdown
responses include `Vary: Accept`; absent, wildcard-only, or HTML-preferred
headers continue to receive the normal HTML page.

Dynamic content pages expose source-backed `Last-Modified` validators.
Markdown representations and JSON sidecars also expose strong `ETag` values;
matching `If-Modified-Since` or `If-None-Match` requests return `304` without a
response body.
