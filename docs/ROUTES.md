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

Route definitions are grouped in `furatena.catalog.route_registrars` by public,
author, search, catalog/export, media, and error surfaces. `DocsApp` composes
those registrars with its dynamic mount and localized routes before app freeze.
Handlers that require direct source inspection, such as the Open Graph image
route, are module-level functions.
