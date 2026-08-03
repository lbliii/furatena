# Presentation packs

Presentation packs are Furatena's versioned extension boundary for HTML presentation. They do
not change Content IR, graph identity, routes, visibility, search documents, PDF semantics, or
agent corpora. One site resolves exactly one complete layout, zero or one skin, and an ordered
list of sparse overrides.

## Site configuration

Use the site-global `presentation` block in `docs.yaml`:

```yaml
presentation:
  layout: product-layout
  skin: product-brand
  overrides:
    - accessibility-overrides
    - launch-campaign-overrides
  trusted_capabilities:
    - scripts
```

Override order is first-match-wins. Project `templates/` remains the highest-precedence sparse
compatibility override during the v1 migration window. A selected complete layout follows the
ordered override packs; framework templates remain the final fallback.

Presentation is site-global. A `delivery.mounts.<id>.theme` value that differs from the global
selection fails validation with a migration diagnostic. Publish mounts that require distinct
presentation as separate sites.

## Built-in complete layouts

Furatena ships two immutable complete layouts in the installed package:

- `docs` is the default. It is the full documentation experience used by the Furatena catalog,
  including rich navigation, search, API-reference, collection, portal, error, and empty states.
  Existing sites that only configure `theme.use: lagoon` keep Lagoon as the compatibility skin.
- `vanilla` is a restrained, neutral layout with the same eight semantic view kinds. It uses
  native links and GET search forms, one layout stylesheet, responsive system typography, visible
  focus states, reduced-motion handling, and print rules. Select it with
  `presentation: {layout: vanilla}`; no skin is implied by `theme.use` once a layout is explicit.

Both layouts render live, frozen, static, fragment, and PDF inputs without a project `theme/` or
`templates/` fixture. Their presentation choice does not change Content IR, visibility filtering,
search documents, catalog JSON, or agent text. Lagoon remains a supported skin and can be selected
explicitly with `presentation.skin` when a complete layout should use it.

To customize a built-in, shadow only the files you own in project `templates/` or publish a sparse
override pack. Installed layout files are never copied into or rewritten inside the project, so a
Furatena upgrade cannot overwrite adopter-owned templates.

## Repository-local packs

Repository-local packs live anywhere below `presentation/` and require no Python package:

```text
presentation/product-layout/
├── presentation-pack.json
├── shell.html
├── layouts/
└── views/
```

The selector is the manifest `id`, not a filesystem path. Discovery is deterministic across
repository-local manifests, Furatena's packaged manifests, `furatena.presentation_packs` entry
points, and legacy `furatena.themes` entry points. A duplicate `id` is an error even when the
duplicate pack is not selected.

Managed deployments require local packs and every declared file to remain within the active
site generation. Absolute paths, parent traversal, symlinks, undeclared files, and cross-root
selection fail before serving.

## Manifest v1

`presentation-pack.json` is validated by the shipped
`presentation-pack-v1.schema.json` schema. Schema version, pack semantic version, render-context
API version, and compatible Furatena runtime range are independent fields.

```json
{
  "schema_version": 1,
  "id": "product-layout",
  "version": "1.2.3",
  "type": "layout",
  "render_context_api_version": "1",
  "runtime": ">=0.1.0,<0.2.0",
  "templates": {
    "doc": "views/doc.html",
    "doc_list": "views/doc_list.html",
    "collection": "views/collection.html",
    "changelog": "views/changelog.html",
    "api_reference": "views/api_reference.html",
    "page": "views/page.html",
    "home": "views/home.html",
    "portal": "views/portal.html"
  },
  "assets": [],
  "capabilities": ["templates"],
  "requires_trust": [],
  "contract": {
    "render_modes": ["full", "fragment"],
    "slots": ["head", "scripts", "shell", "content"],
    "semantic_hooks": ["main", "page-root"],
    "component_api": "chirp-ui@0.11"
  }
}
```

A `layout` must own every built-in `VIEW_KINDS` identity. Existing view lint then checks the
declared templates against the same typed contexts and required `page_root` block used by the
built-in layout. A `skin` cannot own views. An `override` is sparse and may declare any subset.

Skin assets use one declaration per role: `tokens`, `styles`, `directives`, `scripts`, `fonts`,
or `branding`. Scripts must declare both the `scripts` capability and `requires_trust: [scripts]`;
the site must separately grant `presentation.trusted_capabilities: [scripts]` after review.
Route and asset hooks follow the same explicit-trust rule.

## Precedence and compatibility

Template resolution is first-match-wins:

1. Project `templates/` compatibility overrides.
2. `presentation.overrides` in configured order.
3. The selected skin's compatibility templates, when present.
4. Existing project `theme/` templates.
5. The complete `presentation.layout`.
6. A managed platform `theme/`, when separate from the site root.
7. Furatena framework templates and Chirp UI components.

`theme.use` remains a deprecated skin selector and public `furatena.themes` entry points are
adapted to presentation records during the compatibility window. `theme.id: furatena` and
`theme.id: chirp` remain compatibility aliases for the docs-core/default-layout bundle. Existing
project `theme/` and `templates/` directories continue to work. Migrate reusable packs to a v1
manifest and select them through `presentation.layout` / `presentation.skin`.

## Provenance and caching

Resolution produces one immutable, path-free record containing each layer's identity, version,
source kind, compatibility versions, capabilities, trust grants, and content digest. The digest
uses file contents and relative names, never mtimes or absolute paths. It participates in renderer
fingerprints and is copied into freeze and static deployment manifests plus HTML presentation meta
tags. It is intentionally absent from Content IR and agent-facing corpus payloads.
