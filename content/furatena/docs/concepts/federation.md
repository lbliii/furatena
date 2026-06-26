---
title: Federation
description: Multiple content mounts and cross-mount linking
draft: false
weight: 50
lang: en
type: doc
category: concepts
---

Federation lets one Furatena app serve multiple corpora:

```yaml
mounts:
  - id: furatena
    content_root: ../content/furatena
    default: true
  - id: shared
    content_root: content/shared
    url_prefix: /shared
```

Cross-mount wikilinks use `[[mount:slug|label]]` syntax. The `/portal/` view lists all mounts.

Shared reference content can live in a dedicated mount (for example `/shared/`).
