---
title: Reference inventories
description: Cross-mount wikilinks, domain roles, and legacy URL rewrites (Wave E).
---

# Reference inventories

Wave E connects federated mounts, external symbol inventories, and inline reference roles.

## Cross-mount wikilinks

Link to the Chirp docs mount from shared content:

[[chirp:docs/get-started/read-through|Read-through guide]]

## Domain roles

Resolve Python stdlib symbols from a cached `objects.inv` inventory:

{py}`os.path.join`

Explicit catalog xref:

{xref}`chirp:docs/get-started/read-through`

## Legacy deploy URLs

Markdown links using legacy `/chirp/docs/` prefixes rewrite to `/docs/` at index time:

[Install](/chirp/docs/get-started/installation/)
